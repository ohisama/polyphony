﻿from collections import defaultdict, namedtuple
from env import env
from symbol import Symbol
from logging import getLogger
from irvisitor import IRVisitor
from block import Block
from copy import copy
from ir import ARRAY, MOVE
logger = getLogger(__name__)

FunctionParam = namedtuple('FunctionParam', ('sym', 'copy', 'defval'))

class Scope:
    ordered_scopes = []
    
    @classmethod
    def create(cls, parent, name = None, attributes = []):
        if name is None:
            name = "unnamed_scope" + str(len(env.scopes))
        s = Scope(parent, name, attributes)
        assert s.name not in env.scopes
        env.append_scope(s)
        return s

    @classmethod
    def get_scopes(cls, contain_global=False, bottom_up=True):
        def ret_helper(contain_global, bottom_up):
            start = 0 if contain_global else 1
            scopes = cls.ordered_scopes[start:]
            if bottom_up:
                scopes.reverse()
            return scopes

        def set_order(scope, order, ordered):
            if order > scope.order:
                scope.order = order
                ordered.add(scope)
            elif scope in ordered:
                return
            order += 1
            for s in scope.callee_scopes:
                set_order(s, order, ordered)
        
        top = env.scopes['@top']
        top.order = 0
        ordered = set()
        for f in top.children:
            set_order(f, 1, ordered)
        cls.ordered_scopes = sorted(env.scopes.values(), key=lambda s: s.order)
        return ret_helper(contain_global, bottom_up)


    def __init__(self, parent, name, attributes):
        self.name = name
        self.orig_name = name
        self.parent = parent
        if parent:
            self.name = parent.name + "." + name
            parent.append_child(self)

        self.funcnames = []
        self.attributes = attributes
        self.symbols = {}
        self.params = []
        self.return_type = None
        self.blocks = []
        self.blk_grp_stack = []
        self.children = []
        self.usedef = None
        self.loop_nest_tree = None
        self.loop_infos = {}
        self.calls = defaultdict(set)
        self.blk_grp_instances = []
        self.stgs = []
        self.order = -1
        self.callee_scopes = set()
        self.caller_scopes = set()

    def __str__(self):
        s = '\n================================\n'
        attributes = ", ".join([att for att in self.attributes])
        if self.parent:
            s += "Scope: {}, parent={} ({})\n".format(self.orig_name, self.parent.name, attributes)
        else:
            s += "Scope: {} ({})\n".format(self.orig_name, attributes)

        s += ", ".join([str(sym) for sym in self.symbols])
        s += "\n"
        s += '================================\n'
        s += 'Parameters\n'
        for p, copy, val in self.params:
            s += '{} = {}\n'.format(p, val)
        s += "\n"
        s += '================================\n'
        for blk in self.blocks:
            s += str(blk)

        s += '================================\n'
        s += 'Block Group\n'
        for bl in self.blk_grp_instances:
            s += str(bl)+'\n'

        s += '================================\n'    
        return s

    def __repr__(self):
        return self.name

    def __lt__(self, other):
        return self.order < other.order

    def clone(self, postfix):
        s = Scope(self.parent, self.orig_name + '_' + postfix, self.attributes)
        s.funcnames = self.funcnames

        # clone symbols
        symbol_map = {}
        for orig_sym in self.symbols.values():
            new_sym = Symbol.new(orig_sym.name, s)
            new_sym.typ = orig_sym.typ
            s.symbols[new_sym.name] = new_sym
            symbol_map[orig_sym] = new_sym

        s.params = [FunctionParam(symbol_map[p], symbol_map[copy], defval.clone() if defval else None) for p, copy, defval in self.params]
        s.return_type = self.return_type

        # clone block group
        s.blk_grp_instances = []
        group_map = {}
        for orig_grp in self.blk_grp_instances:
            new_grp = BlockGroup(orig_grp.name)
            s.blk_grp_instances.append(new_grp)
            group_map[orig_grp] = new_grp
        for orig_grp in self.blk_grp_instances:
            new_grp = group_map[orig_grp]
            if orig_grp.parent:
                new_grp.parent = group_map[orig_grp.parent]

        # clone block
        s.blocks = []
        block_map = {}
        stm_map = {}
        for orig_b in self.blocks:
            new_b = Block(orig_b.name)
            new_stms = []
            for orig_stm in orig_b.stms:
                # TODO: replace symbols
                new_stm = orig_stm.clone()
                new_stm.block = new_b
                new_stms.append(new_stm)
                stm_map[orig_stm] = new_stm
            new_b.stms = new_stms
            new_b.order = orig_b.order
            new_b.group = group_map[orig_b.group]
            new_b.set_scope(s)
            s.blocks.append(new_b)
            block_map[orig_b] = new_b
        # remake cfg
        for orig_b in self.blocks:
            new_b = block_map[orig_b]
            for orig_succ in orig_b.succs:
                new_b.succs.append(block_map[orig_succ])
            for orig_succ in orig_b.succs_loop:
                new_b.succs_loop.append(block_map[orig_succ])
            for orig_pred in orig_b.preds:
                new_b.preds.append(block_map[orig_pred])
            for orig_pred in orig_b.preds_loop:
                new_b.preds_loop.append(block_map[orig_pred])

        # clone loop info
        for orig_head, orig_li in self.loop_infos.items():
            new_head = block_map[orig_head]
            new_li = LoopBlockInfo(new_head, orig_li.name)
            s.loop_infos[new_head] = new_li
            for orig_body in orig_li.bodies:
                new_body = block_map[orig_body]
                new_li.bodies.add(new_body)
            for orig_break in orig_li.breaks:
                new_break = block_map[orig_break]
                new_li.breaks.append(new_break)
            for orig_return in orig_li.returns:
                new_return = block_map[orig_return]
                new_li.returns.append(new_return)

            if orig_li.exit in block_map:
                new_li.exit = block_map[orig_li.exit]
            new_li.defs = None
            new_li.uses = None

        s.children = list(self.children)
        s.usedef = None

        new_calls = defaultdict(set)
        for func_sym, inst_names in self.calls.items():
            new_func_sym = symbol_map[func_sym]
            new_calls[new_func_sym] = copy(inst_names)
        s.calls = new_calls
        s.order = self.order
        s.callee_scopes = list(self.callee_scopes)
        s.caller_scopes = list(self.caller_scopes)

        sym_replacer = SymbolReplacer(symbol_map)
        sym_replacer.process(s)

        s.parent.append_child(s)
        env.append_scope(s)
        return s

    def add_funcname(self, name):
        self.funcnames.append(name)

    def is_funcname(self, name):
        return name in self.funcnames

    def find_scope_having_funcname(self, name):
        if name in self.funcnames:
            return self
        elif self.parent:
            return self.parent.find_scope_having_funcname(name)
        else:
            return None

    def find_func_scope(self, func_name):
        if self.orig_name == func_name:
            return self
        for child in self.children:
            if child.orig_name == func_name:
                return child
        if self.parent:
            return self.parent.find_func_scope(func_name)
        else:
            return None

    def add_callee_scope(self, callee):
        self.callee_scopes.add(callee)
        callee.caller_scopes.add(self)

    def add_sym(self, name):
        if name in self.symbols:
            raise RuntimeError("symbol '{}' is already registered ".format(name))
        sym = Symbol.new(name, self)
        self.symbols[name] = sym
        return sym

    def add_temp(self, name):
        if name in self.symbols:
            raise RuntimeError("symbol '{}' is already registered ".format(name))
        sym = Symbol.newtemp(name, self)
        self.symbols[sym.name] = sym
        return sym

    def find_sym(self, name):
        if name in self.symbols:
            return self.symbols[name]
        elif self.parent:
            found = self.parent.find_sym(name)
            #if found:
            #    raise RuntimeError("'{}' is in the outer scope. Polyphony supports local name scope only.".format(name))
            return found
        return None

    def has_sym(self, name):
        return name in self.symbols

    def gen_sym(self, name):
        sym = self.find_sym(name)
        if not sym:
            sym = self.add_sym(name)
        return sym

    def inherit_sym(self, orig_sym, new_name):
        new_sym = orig_sym.scope.gen_sym(new_name)
        new_sym.typ = orig_sym.typ
        new_sym.ancestor = orig_sym
        return new_sym

    def qualified_name(self):
        n = ""
        if self.parent is not None:
            n = self.parent.qualified_name() + "_"
        n += self.name
        return n

    def remove_block(self, blk):
        self.blocks.remove(blk)
        blk.group.remove(blk)
        if not blk.group.blocks:
            self.blk_grp_instances.remove(blk.group)

    def append_block(self, blk):
        blk.set_scope(self)
        self.blocks.append(blk)
        self.blk_grp_stack[-1].append(blk)

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

    def append_call(self, func_sym, inst_name):
        self.calls[func_sym].add(inst_name)

    def dfgs(self, bottom_up=False):
        infos = sorted(self.loop_infos.values(), key=lambda l:l.name, reverse=bottom_up)
        return [info.dfg for info in infos]

    def begin_block_group(self, tag):
        grp = self._create_block_group(tag)
        #if self.blk_grp_stack:
        #    grp.parent = self.blk_grp_stack[-1]
        self.blk_grp_stack.append(grp)

    def end_block_group(self):
        self.blk_grp_stack.pop()

    def _create_block_group(self, tag):
        name = 'grp_' + tag + str(len(self.blk_grp_instances))
        bl = BlockGroup(name)
        self.blk_grp_instances.append(bl)
        return bl

    def create_loop_info(self, head):
        name = 'L' + str(len(self.loop_infos))
        li = LoopBlockInfo(head, name)
        self.loop_infos[head] = li
        return li

    def find_loop_head(self, block):
        for head, bodies in self.loop_infos.items():
            if head is block:
                return head
            for b in bodies:
                if block is b:
                    return head
        return None

    def is_testbench(self):
        return 'testbench' in self.attributes

    def is_main(self):
        return 'top' in self.attributes

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

    def append_loop_counter(self, loop):
        self.loop_counter.append(loop)


class BlockGroup:
    def __init__(self, name):
        self.name = name
        self.blocks = []
        self.parent = None

    def __str__(self):
        if self.parent:
            return '{} ({}) parent:{}'.format(self.name, ', '.join([blk.name for blk in self.blocks]), self.parent.name)
        else:
            return '{} ({})'.format(self.name, ', '.join([blk.name for blk in self.blocks]))
    def append(self, blk):
        self.blocks.append(blk)
        blk.group = self

    def remove(self, blk):
        self.blocks.remove(blk)

class LoopBlockInfo:
    def __init__(self, head, name):
        self.head = head
        self.bodies = set()
        self.breaks = []
        self.returns = []
        self.exit = None
        self.name = name
        self.defs = None
        self.uses = None

    def append_break(self, brk):
        self.breaks.append(brk)

    def append_return(self, blk):
        self.returns.append(blk)

    def append_bodies(self, bodies):
        assert isinstance(bodies, set)
        self.bodies = self.bodies.union(bodies)


class SymbolReplacer(IRVisitor):
    def __init__(self, sym_map):
        super().__init__()
        self.sym_map = sym_map

    def visit_TEMP(self, ir):
        ir.sym = self.sym_map[ir.sym]


