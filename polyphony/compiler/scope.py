﻿from collections import defaultdict, namedtuple
from copy import copy
from .common import Tagged
from .env import env
from .symbol import Symbol
from .irvisitor import IRVisitor
from .block import Block
from .ir import *
from .signal import Signal

from logging import getLogger
logger = getLogger(__name__)

FunctionParam = namedtuple('FunctionParam', ('sym', 'copy', 'defval'))

class Worker:
    def __init__(self, scope, args):
        self.scope = scope
        self.args = args

class Scope(Tagged):
    ordered_scopes = []
    TAGS = {
        'global', 'function', 'class', 'method', 'ctor',
        'callable', 'returnable', 'mutable',
        'testbench',
        'module', 'worker',
        'port', 'lib', 'namespace',
        'function_module',
        'inlinelib',
    }

    @classmethod
    def create(cls, parent, name, tags, lineno = 0):
        if name is None:
            name = "unnamed_scope" + str(len(env.scopes))
        s = Scope(parent, name, tags, lineno)
        assert s.name not in env.scopes
        env.append_scope(s)
        return s

    @classmethod
    def get_scopes(cls, bottom_up=True, with_global=False, with_class=False, with_lib=False):
        def ret_helper():
            scopes = cls.ordered_scopes[:]
            if not with_global:
                scopes.remove(Scope.global_scope())
            if not with_class:
                scopes = [s for s in scopes if not s.is_class()]
            if not with_lib:
                scopes = [s for s in scopes if not s.is_lib()]
            if bottom_up:
                scopes.reverse()
            return scopes

        cls.reorder_scopes()
        cls.ordered_scopes = sorted(env.scopes.values())

        return ret_helper()

    @classmethod
    def reorder_scopes(cls):
        def set_order(scope, order, ordered):
            if order > scope.order:
                scope.order = order
                ordered.add(scope)
            elif scope in ordered:
                return
            order += 1
            for s in scope.children:
                set_order(s, order, ordered)
            for s in scope.callee_scopes:
                set_order(s, order, ordered)
        top = cls.global_scope()
        top.order = 0
        ordered = set()
        for f in top.children:
            set_order(f, 1, ordered)

    @classmethod
    def get_class_scopes(cls, bottom_up=True):
        return [s for s in cls.get_scopes(bottom_up=bottom_up, with_class=True) if s.is_class()]

    @classmethod
    def global_scope(cls):
        return env.scopes['@top']

    @classmethod
    def is_unremoveable(cls, s):
        return s.is_global() or s.is_class() or s.is_testbench() or s.is_worker() or (s.is_method() and s.parent.is_module())

    def __init__(self, parent, name, tags, lineno):
        super().__init__(tags, Scope.TAGS)
        self.name = name
        self.orig_name = name
        self.parent = parent
        if parent:
            self.name = parent.name + "." + name
            parent.append_child(self)

        self.lineno = lineno
        self.symbols = {}
        self.params = []
        self.return_type = None
        self.entry_block = None
        self.exit_block = None
        self.children = []
        self.bases = []
        self.usedef = None
        self.loop_nest_tree = None
        self.callee_instances = defaultdict(set)
        self.stgs = []
        self.order = -1
        self.callee_scopes = set()
        self.caller_scopes = set()
        self.module_info = None
        #self.field_access = defaultdict(set)
        self.signals = {}
        self.block_count = 0
        self.class_fields = {}
        self.paths = []
        self.workers = {}
        self.asap_latency = -1

    def __str__(self):
        s = '\n================================\n'
        tags = ", ".join([att for att in self.tags])
        if self.parent:
            s += "Scope: {}, parent={} ({})\n".format(self.orig_name, self.parent.name, tags)
        else:
            s += "Scope: {} ({})\n".format(self.orig_name, tags)

        s += ", ".join([str(sym) for sym in self.symbols])
        s += "\n"
        s += '================================\n'
        s += 'Parameters\n'
        for p, copy, val in self.params:
            s += '{}:{} = {}\n'.format(p, p.typ, val)
        s += "\n"
        s += '================================\n'
        for blk in self.traverse_blocks(longitude=True):
            s += str(blk)
        s += '================================\n'    
        return s

    def __repr__(self):
        return self.name

    def __lt__(self, other):
        return (self.order, self.lineno) < (other.order, other.lineno)

    def clone_symbols(self, scope, postfix = ''):
        symbol_map = {}
        for orig_sym in self.symbols.values():
            new_sym = Symbol.new(orig_sym.name + postfix, scope)
            new_sym.typ = orig_sym.typ
            assert new_sym.name not in scope.symbols
            scope.symbols[new_sym.name] = new_sym
            symbol_map[orig_sym] = new_sym
        return symbol_map

    def clone_blocks(self, scope):
        block_map = {}
        stm_map = {}
        for b in self.traverse_blocks(full=True):
            block_map[b] = b.clone(scope, stm_map)
        for b in self.traverse_blocks(full=True):
            b_clone = block_map[b]
            b_clone.reconnect(block_map)

        # jump target
        for stm in stm_map.values():
            if stm.is_a(JUMP):
                stm.target = block_map[stm.target]
            elif stm.is_a(CJUMP):
                stm.true = block_map[stm.true]
                stm.false = block_map[stm.false]
            elif stm.is_a(MCJUMP):
                stm.targets = [block_map[t] for t in stm.targets]
            elif stm.is_a(PHI):
                stm.args = [(arg, block_map[blk]) for arg, blk in stm.args]
        return block_map

    def clone(self, prefix, postfix):
        assert not self.is_class()

        name = prefix + '_' if prefix else ''
        name += self.orig_name
        name = name + '_' + postfix if postfix else name
        s = Scope(self.parent, name, self.tags, self.lineno)

        symbol_map = self.clone_symbols(s)

        s.params = [FunctionParam(symbol_map[p], symbol_map[copy], defval.clone() if defval else None) for p, copy, defval in self.params]
        s.return_type = self.return_type

        block_map = self.clone_blocks(s)
        s.entry_block = block_map[self.entry_block]
        s.exit_block = block_map[self.exit_block]

        s.children = list(self.children)
        for child in s.children:
            child.parent = s
        s.bases = list(self.bases)
        s.usedef = None

        new_callee_instances = defaultdict(set)
        for func_sym, inst_names in self.callee_instances.items():
            new_func_sym = symbol_map[func_sym]
            new_callee_instances[new_func_sym] = copy(inst_names)
        s.callee_instances = new_callee_instances
        s.order = self.order
        s.callee_scopes = set(self.callee_scopes)
        s.caller_scopes = set(self.caller_scopes)

        sym_replacer = SymbolReplacer(symbol_map)
        sym_replacer.process(s)

        s.parent.append_child(s)
        env.append_scope(s)
        return s

    def find_child(self, name):
        for child in self.children:
            if child.orig_name == name:
                return child
        return None

    def find_parent_scope(self, name):
        if self.find_child(name):
            return self
        elif self.parent:
            return self.parent.find_parent_scope(name)
        else:
            return None

    def find_scope(self, name):
        if self.orig_name == name:
            return self
        child = self.find_child(name)
        if child:
            return child
        if self.parent:
            return self.parent.find_scope(name)
        return None

    def add_callee_scope(self, callee):
        self.callee_scopes.add(callee)
        if callee is None:
            assert False
        callee.caller_scopes.add(self)

    def add_sym(self, name):
        if name in self.symbols:
            raise RuntimeError("symbol '{}' is already registered ".format(name))
        sym = Symbol.new(name, self)
        self.symbols[name] = sym
        return sym

    def del_sym(self, name):
        if name in self.symbols:
            del self.symbols[name]

    def import_sym(self, sym):
        if sym.name in self.symbols and sym is not self.symbols[sym.name]:
            raise RuntimeError("symbol '{}' is already registered ".format(name))
        self.symbols[sym.name] = sym

    def add_temp(self, name):
        sym = Symbol.newtemp(name, self)
        self.symbols[sym.name] = sym
        return sym

    def find_sym(self, name):
        names = name.split('.')
        if len(names) > 1:
            return self.find_sym_r(names)
        if name in self.symbols:
            return self.symbols[name]
        elif self.parent:
            found = self.parent.find_sym(name)
            return found
        return None

    def find_sym_r(self, names):
        name = names[0]
        sym = self.find_sym(name)
        if sym and len(names) > 1:
            if sym.typ.is_containable():
                return sym.typ.get_scope().find_sym_r(names[1:])
            else:
                return None
        return sym

    def has_sym(self, name):
        return name in self.symbols

    def gen_sym(self, name):
        sym = self.find_sym(name)
        if not sym:
            sym = self.add_sym(name)
        return sym

    def rename_sym(self, old, new):
        assert old in self.symbols
        sym = self.symbols[old]
        del self.symbols[old]
        sym.name = new
        self.symbols[new] = sym
        return sym

    def inherit_sym(self, orig_sym, new_name):
        new_sym = orig_sym.scope.gen_sym(new_name)
        new_sym.typ = orig_sym.typ
        if orig_sym.ancestor:
            new_sym.ancestor = orig_sym.ancestor
        else:
            new_sym.ancestor = orig_sym
        return new_sym

    def gen_refsym(self, orig_sym):
        new_name =  Symbol.ref_prefix + '_' + orig_sym.name
        new_sym = self.gen_sym(new_name)
        new_sym.typ = orig_sym.typ
        if orig_sym.ancestor:
            new_sym.ancestor = orig_sym.ancestor
        else:
            new_sym.ancestor = orig_sym
        return new_sym

    def qualified_name(self):
        n = ""
        if self.parent is not None:
            n = self.parent.qualified_name() + "_"
        n += self.name
        return n

    def set_entry_block(self, blk):
        assert self.entry_block is None
        self.entry_block = blk

    def set_exit_block(self, blk):
        assert self.exit_block is None
        self.exit_block = blk

    def traverse_blocks(self, full=False, longitude=False):
        assert len(self.entry_block.preds) == 0
        visited = set()
        yield from self.entry_block.traverse(visited, full, longitude)

    def append_child(self, child_scope):
        if child_scope not in self.children:
            self.children.append(child_scope)

    def add_param(self, sym, copy, defval):
        self.params.append(FunctionParam(sym, copy, defval))

    def has_param(self, sym):
        name = sym.name.split('#')[0]
        for p, _, _ in self.params:
            if p.name == name:
                return True
        return False

    def get_param_index(self, sym):
        name = sym.name.split('#')[0]
        for i, (p, _, _) in enumerate(self.params):
            if p.name == name:
                return i
        return -1

    def append_callee_instance(self, callee_scope, inst_name):
        self.callee_instances[callee_scope].add(inst_name)

    def dfgs(self, bottom_up=False):
        def collect_dfg(dfg, ds):
            ds.append(dfg)
            for c in dfg.children:
                collect_dfg(c, ds)
        ds = []
        collect_dfg(self.top_dfg, ds)
        return ds

    def find_ctor(self):
        assert self.is_class()
        for child in self.children:
            if child.is_ctor():
                return child
        return None

    def is_global(self):
        return self is Scope.global_scope()

    def is_containable(self):
        return self.is_namespace() or self.is_class()

    def find_stg(self, name):
        assert self.stgs
        for stg in self.stgs:
            if stg.name == name:
                return stg
        return None

    def get_main_stg(self):
        assert self.stgs
        for stg in self.stgs:
            if stg.is_main():
                return stg
        return None

    def gen_sig(self, name, width, tag = None):
        if name in self.signals:
            sig = self.signals[name]
            sig.width = width
            if tag:
                sig.add_tag(tag)
            return sig
        sig = Signal(name, width, tag)
        self.signals[name] = sig
        return sig

    def signal(self, name):
        if name in self.signals:
            return self.signals[name]
        return None

    def rename_sig(self, old, new):
        assert old in self.signals
        sig = self.signals[old]
        del self.signals[old]
        sig.name = new
        self.signals[new] = sig
        return sig

    def add_class_field(self, f, init_stm):
        assert self.is_class()
        self.class_fields[f] = init_stm

    def append_worker(self, key, worker_scope, worker_args):
        self.workers[key] = Worker(worker_scope, worker_args)
        if not worker_scope.is_worker():
            worker_scope.add_tag('worker')

class SymbolReplacer(IRVisitor):
    def __init__(self, sym_map):
        super().__init__()
        self.sym_map = sym_map

    def visit_TEMP(self, ir):
        ir.sym = self.sym_map[ir.sym]

    def visit_ATTR(self, ir):
        ir.attr = self.sym_map[ir.attr]


