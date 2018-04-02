from .ahdl import *
from .ahdlhelper import AHDLVarReplacer
from .ahdlusedef import AHDLUseDefDetector
from .block import Block
from .ir import *
from .stg import State, STGItemBuilder, ScheduledItemQueue


class PipelineState(State):
    def __init__(self, name, stages, first_valid_signal, stg, is_finite_loop=False):
        assert isinstance(name, str)
        self.name = name
        self.stages = stages
        if first_valid_signal:
            self.valid_signals = {0:first_valid_signal}
        else:
            self.valid_signals = {}
        self.ready_signals = {}
        self.enable_signals = {}
        self.hold_signals = {}
        self.last_signals = {}
        self.exit_signals = {}
        self.stg = stg
        self.is_finite_loop = is_finite_loop

    def __str__(self):
        s = '---------------------------------\n'
        s += '{}\n'.format(self.name)

        if self.stages:
            for stage in self.stages:
                lines = ['---{}---'.format(stage.name)]
                strcodes = '\n'.join(['{}'.format(code) for code in stage.codes])
                lines += strcodes.split('\n')
                s += '\n'.join(['  {}'.format(line) for line in lines])
                s += '\n'
        else:
            pass
        s += '\n'
        return s

    def _pipeline_signal(self, signal_name, signals, idx, is_reg):
        assert idx >= 0
        if idx not in signals:
            stage_name = self.name + '_{}'.format(idx)
            name = '{}_{}'.format(stage_name, signal_name)
            if is_reg:
                tags = {'reg', 'pipeline_ctrl'}
            else:
                tags = {'net', 'pipeline_ctrl'}
            new_sig = self.stg.hdlmodule.gen_sig(name, 1, tags)
            signals[idx] = new_sig
        return signals[idx]

    def valid_signal(self, idx):
        return self._pipeline_signal('valid', self.valid_signals, idx, True)

    def valid_exp(self, idx):
        ready = self.ready_signal(idx)
        if idx > 0:
            # hold ? ready : ready & prev_valid
            hold = self.hold_signal(idx)
            valid_prev = self.valid_signal(idx - 1)
            return AHDL_IF_EXP(AHDL_VAR(hold, Ctx.LOAD),
                               AHDL_VAR(ready, Ctx.LOAD),
                               AHDL_OP('BitAnd',
                                       AHDL_VAR(ready, Ctx.LOAD),
                                       AHDL_VAR(valid_prev, Ctx.LOAD)))
        else:
            return AHDL_VAR(ready, Ctx.LOAD)

    def ready_signal(self, idx):
        return self._pipeline_signal('ready', self.ready_signals, idx, False)

    def enable_signal(self, idx):
        return self._pipeline_signal('enable', self.enable_signals, idx, False)

    def hold_signal(self, idx):
        return self._pipeline_signal('hold', self.hold_signals, idx, True)

    def last_signal(self, idx):
        return self._pipeline_signal('last', self.last_signals, idx, True)

    def exit_signal(self, idx):
        return self._pipeline_signal('exit', self.exit_signals, idx, True)

    def new_stage(self, step, codes):
        name = self.name + '_{}'.format(step)
        s = PipelineStage(name, step, codes, self.stg, self)
        self.stages.append(s)
        assert len(self.stages) == step + 1, 'stages {} step {}'.format(len(self.stages), step)
        return s

    def traverse(self):
        for s in self.stages:
            for c in s.codes:
                yield c

    def resolve_transition(self, next_state, blk2states):
        end_stage = self.stages[-1]
        code = end_stage.codes[-1]
        if code.is_a(AHDL_TRANSITION_IF):
            for i, codes in enumerate(code.codes_list):
                transition = codes[-1]
                assert transition.is_a(AHDL_TRANSITION)
                if isinstance(transition.target, Block):
                    target_state = blk2states[transition.target][0]
                    transition.target = target_state
                else:
                    pass
        elif code.is_a(AHDL_TRANSITION):
            pass
        else:
            assert False
        transition = code

        move_transition = False
        for s in self.stages:
            for code in s.codes:
                if code.is_a(AHDL_META_WAIT):
                    if transition:
                        code.transition = transition
                        move_transition = True
                    else:
                        code.transition = AHDL_TRANSITION(next_state)
            if move_transition:
                s.codes.pop()
        return next_state


class PipelineStage(State):
    def __init__(self, name, step, codes, stg, parent_state):
        super().__init__(name, step, codes, stg)
        self.parent_state = parent_state
        self.has_enable = False
        self.enable = None
        self.has_hold = False
        self.is_source = False


class PipelineStageBuilder(STGItemBuilder):
    def __init__(self, scope, stg, blk2states):
        super().__init__(scope, stg, blk2states)

    def build(self, dfg, is_main):
        blk_name = dfg.region.head.nametag + str(dfg.region.head.num)
        prefix = self.stg.name + '_' + blk_name + '_P'
        pstate = PipelineState(prefix, [], None, self.stg, is_finite_loop=self.is_finite_loop)

        self.scheduled_items = ScheduledItemQueue()
        self._build_scheduled_items(dfg)
        self._build_pipeline_stages(prefix, pstate, is_main)

        for blk in dfg.region.blocks():
            self.blk2states[blk] = [pstate]

        self.stg.states.append(pstate)
        self.stg.init_state = pstate
        self.stg.finish_state = pstate

        # customize point
        self.post_build(dfg, is_main, pstate)

        if not pstate.stages[-1].codes[-1].is_a([AHDL_TRANSITION, AHDL_TRANSITION_IF]):
            pipe_end_stm = AHDL_TRANSITION(pstate)
            pstate.stages[-1].codes.append(pipe_end_stm)

    def post_build(self, dfg, is_main, pstate):
        pass

    def _build_pipeline_stages(self, prefix, pstate, is_main):
        for (step, items) in sorted(self.scheduled_items.queue.items()):
            codes = []
            for item, _ in items:
                assert isinstance(item, AHDL)
                codes.append(item)
            self._make_stage(pstate, step, codes)

        for stage in pstate.stages:
            self._add_control_chain(pstate, stage)

        # analysis and inserting register slices between pileline stages
        stm2stage_num = self._make_stm2stage_num(pstate)
        detector = AHDLUseDefDetector()
        for stage in pstate.stages:
            detector.current_state = stage
            for code in stage.traverse():
                detector.visit(code)
        usedef = detector.table
        for sig in usedef.get_all_def_sigs():
            if sig.is_pipeline_ctrl():
                continue
            defs = usedef.get_stms_defining(sig)
            d = list(defs)[0]
            d_stage_n = stm2stage_num[d]
            uses = usedef.get_stms_using(sig)
            use_max_distances = 0
            for u in uses:
                u_stage_n = stm2stage_num[u]
                distance = u_stage_n - d_stage_n
                #assert 0 <= distance, '{} {}'.format(d, u)
                if use_max_distances < distance:
                    use_max_distances = distance
            if (1 < use_max_distances or
                    ((sig.is_induction() or sig.is_net()) and 0 < use_max_distances)):
                self._insert_register_slices(sig, pstate.stages,
                                             d_stage_n, d_stage_n + use_max_distances,
                                             usedef, stm2stage_num)

    def _make_stage(self, pstate, step, codes):
        stage = pstate.new_stage(step, codes)
        guarded_codes = []
        if stage.step == 0 and pstate.is_finite_loop:
            stage.has_enable = True
        for i, c in enumerate(stage.codes[:]):
            if c.is_a(AHDL_SEQ) and c.step == 0:
                if c.factor.is_a([AHDL_IO_READ, AHDL_IO_WRITE]):
                    stage.has_enable = True
                    if c.factor.is_a(AHDL_IO_WRITE):
                        stage.is_source = True
            if stage.step > 0:
                stage.has_hold = True
            if self._check_guard_need(c):
                guarded_codes.append(c)
                stage.codes.remove(c)

        if stage.step == 0:
            if stage.has_enable:
                guard_cond = AHDL_VAR(pstate.ready_signal(0), Ctx.LOAD)
            else:
                guard_cond = AHDL_CONST(1)
        else:
            guard_cond = AHDL_VAR(pstate.valid_signal(stage.step - 1), Ctx.LOAD)
        guard = AHDL_PIPELINE_GUARD(guard_cond, guarded_codes)
        stage.codes.insert(0, guard)

    def _add_control_chain(self, pstate, stage):
        if stage.step == 0:
            v_now = pstate.valid_signal(stage.step)
            if stage.has_enable:
                v_prev = pstate.enable_signal(stage.step)
            else:
                v_prev = None
        else:
            v_now = pstate.valid_signal(stage.step)
            v_prev = pstate.valid_signal(stage.step - 1)
        is_last = stage.step == len(pstate.stages) - 1

        r_now = pstate.ready_signal(stage.step)
        if not is_last:
            r_next = AHDL_VAR(pstate.ready_signal(stage.step + 1), Ctx.LOAD)
        else:
            r_next = AHDL_CONST(1)
        if stage.has_enable:
            en = AHDL_VAR(pstate.enable_signal(stage.step), Ctx.LOAD)
            if stage.is_source:
                if len(pstate.stages) > (stage.step + 1):
                    # ~next_hold & enable
                    inv_next_hold = AHDL_OP('Invert',
                                            AHDL_VAR(pstate.hold_signal(stage.step + 1), Ctx.LOAD))
                    ready_rhs = AHDL_OP('BitAnd', inv_next_hold, en)
                else:
                    ready_rhs = en
            else:
                ready_rhs = AHDL_OP('BitAnd', r_next, en)
        else:
            ready_rhs = r_next
        ready_stm = AHDL_MOVE(AHDL_VAR(r_now, Ctx.STORE), ready_rhs)
        stage.codes.append(ready_stm)

        if stage.has_hold:
            #hold = hold ? (!ready) : (valid & !ready);
            hold = pstate.hold_signal(stage.step)
            if_lhs = AHDL_OP('Invert', AHDL_VAR(r_now, Ctx.LOAD))
            if_rhs = AHDL_OP('BitAnd',
                             AHDL_OP('Invert', AHDL_VAR(r_now, Ctx.LOAD)),
                             AHDL_VAR(v_prev, Ctx.LOAD))
            hold_rhs = AHDL_IF_EXP(AHDL_VAR(hold, Ctx.LOAD), if_lhs, if_rhs)
            hold_stm = AHDL_MOVE(AHDL_VAR(hold, Ctx.STORE), hold_rhs)
            stage.codes.append(hold_stm)

        if not is_last:
            valid_rhs = pstate.valid_exp(stage.step)
            set_valid = AHDL_MOVE(AHDL_VAR(v_now, Ctx.STORE),
                                  valid_rhs)
            stage.codes.append(set_valid)

    def _make_stm2stage_num(self, pstate):
        def _make_stm2stage_num_rec(codes):
            for c in codes:
                stm2stage_num[c] = i
                if c.is_a(AHDL_IF):
                    for codes in c.codes_list:
                        _make_stm2stage_num_rec(codes)
        stm2stage_num = {}
        for i, s in enumerate(pstate.stages):
            _make_stm2stage_num_rec(s.codes)
        return stm2stage_num

    def _check_guard_need(self, ahdl):
        if (ahdl.is_a(AHDL_PROCCALL) or
                ahdl.is_a(AHDL_IF) or
                (ahdl.is_a(AHDL_MOVE) and ((ahdl.dst.is_a(AHDL_VAR) and ahdl.dst.sig.is_reg()) or
                                           ahdl.dst.is_a(AHDL_SUBSCRIPT)))):
            return True
        return False

    def _insert_register_slices(self, sig, stages, start_n, end_n, usedef, stm2stage_num):
        replacer = AHDLVarReplacer()
        defs = usedef.get_stms_defining(sig)
        assert len(defs) == 1
        d = list(defs)[0]
        d_num = stm2stage_num[d]
        is_normal_reg = True if sig.is_reg() and not sig.is_induction() else False
        if is_normal_reg:
            start_n += 1
        for num in range(start_n, end_n + 1):
            if num == d_num:
                continue
            if is_normal_reg and (num - d_num) == 1:
                continue
            new_name = sig.name + '_{}'.format(num)  # use previous stage variable
            tags = sig.tags.copy()
            if 'net' in tags:
                tags.remove('net')
                tags.add('reg')
            self.hdlmodule.gen_sig(new_name, sig.width, tags, sig.sym)
        for u in usedef.get_stms_using(sig):
            num = stm2stage_num[u]
            if num == d_num:
                continue
            if is_normal_reg and (num - d_num) == 1:
                continue
            new_name = sig.name + '_{}'.format(num)
            new_sig = self.hdlmodule.signal(new_name)
            replacer.replace(u, sig, new_sig)
        for num in range(start_n, end_n):
            # first slice uses original
            if num == start_n:
                prev_sig = sig
            else:
                prev_name = sig.name + '_{}'.format(num)
                prev_sig = self.hdlmodule.signal(prev_name)
            cur_name = sig.name + '_{}'.format(num + 1)
            cur_sig = self.hdlmodule.signal(cur_name)
            slice_stm = AHDL_MOVE(AHDL_VAR(cur_sig, Ctx.STORE),
                                  AHDL_VAR(prev_sig, Ctx.LOAD))
            guard = stages[num].codes[0]
            assert guard.is_a(AHDL_PIPELINE_GUARD)
            guard.codes_list[0].append(slice_stm)


class LoopPipelineStageBuilder(PipelineStageBuilder):
    def __init__(self, scope, stg, blk2states):
        super().__init__(scope, stg, blk2states)
        self.is_finite_loop = True

    def post_build(self, dfg, is_main, pstate):
        loop_cnt = self.translator._sym_2_sig(dfg.region.counter)
        cond_defs = self.scope.usedef.get_stms_defining(dfg.region.cond)
        assert len(cond_defs) == 1
        cond_def = list(cond_defs)[0]

        loop_cond = self.translator.visit(cond_def.src, None)
        for stage in pstate.stages:
            self._add_last_signal_chain(pstate, stage, loop_cond)

        # make the start condition of pipeline
        loop_cond = self.translator.visit(cond_def.src, None)
        enable0 = pstate.enable_signal(0)
        loop_enable = AHDL_MOVE(AHDL_VAR(enable0, Ctx.STORE),
                                loop_cond)
        pstate.stages[0].enable = loop_enable

        # make a condition for unexecutable loop
        loop_init = self.translator.visit(dfg.region.init, None)
        loop_cond = self.translator.visit(cond_def.src, None)
        args = []
        for i, a in enumerate(loop_cond.args):
            if a.is_a(AHDL_VAR) and a.sig == loop_cnt:
                args.append(loop_init)
            else:
                args.append(a)
        loop_cond.args = tuple(args)

        # make the exit condition of pipeline
        if stage.step > 0:
            last = pstate.last_signal(stage.step - 1)
            ready = pstate.ready_signal(stage.step)
            loop_end_cond1 = AHDL_OP('BitAnd',
                                     AHDL_VAR(last, Ctx.LOAD),
                                     AHDL_VAR(ready, Ctx.LOAD))
        else:
            last = pstate.last_signal(stage.step)
            loop_end_cond1 = AHDL_VAR(last, Ctx.LOAD)
        loop_end_cond2 = AHDL_OP('Invert', loop_cond)
        loop_end_cond = AHDL_OP('Or', loop_end_cond1, loop_end_cond2)
        conds = [loop_end_cond]
        exit_signal = pstate.exit_signal(len(pstate.stages) - 1)
        codes = [AHDL_MOVE(AHDL_VAR(exit_signal, Ctx.STORE), AHDL_CONST(1))]
        codes_list = [codes]
        loop_end_stm = AHDL_IF(conds, codes_list)
        pstate.stages[-1].codes.append(loop_end_stm)

        # if (exit)
        #    exit <= 0
        #    state <= loop_exit
        conds = [AHDL_VAR(exit_signal, Ctx.LOAD)]
        codes = []
        for i in range(len(pstate.stages)):
            if i == len(pstate.stages) - 1 and len(pstate.stages) > 1:
                break
            l = pstate.last_signal(i)
            codes.append(AHDL_MOVE(AHDL_VAR(l, Ctx.STORE), AHDL_CONST(0)))
        assert len(dfg.region.exits) == 1
        codes.extend([
            AHDL_MOVE(AHDL_VAR(exit_signal, Ctx.STORE), AHDL_CONST(0)),
            AHDL_TRANSITION(dfg.region.exits[0])
        ])
        codes_list = [codes]
        pipe_end_stm = AHDL_TRANSITION_IF(conds, codes_list)
        pstate.stages[-1].codes.append(pipe_end_stm)

    def _build_scheduled_items(self, dfg):
        nodes = []
        for n in dfg.get_scheduled_nodes():
            if n.begin < 0:
                continue
            if n.tag.is_a(CJUMP):
                # remove cjump for the loop
                if n.tag.exp.symbol() is dfg.region.cond:
                    continue
                else:
                    assert False
            nodes.append(n)
        super()._build_scheduled_items(nodes)

    def _add_last_signal_chain(self, pstate, stage, cond):
        is_last = stage.step == len(pstate.stages) - 1
        last = pstate.last_signal(stage.step)
        l_lhs = AHDL_VAR(last, Ctx.STORE)
        if stage.step == 0:
            ready = pstate.ready_signal(stage.step)
            l_rhs = AHDL_OP('Invert', cond)
            set_last = AHDL_MOVE(l_lhs, l_rhs)
            stage.codes.append(set_last)
        elif not is_last:
            prev_last = AHDL_VAR(pstate.last_signal(stage.step - 1), Ctx.LOAD)
            ready = AHDL_VAR(pstate.ready_signal(stage.step), Ctx.LOAD)
            l_rhs = AHDL_OP('BitAnd', prev_last, ready)
            set_last = AHDL_MOVE(l_lhs, l_rhs)
            stage.codes.append(set_last)

    def _add_control_chain__no_stall(self, pstate, stage):
        if stage.step == 0:
            v_now = pstate.valid_signal(stage.step)
            v_prev = None
        else:
            v_now = pstate.valid_signal(stage.step)
            v_prev = pstate.valid_signal(stage.step - 1)
        is_last = stage.step == len(pstate.stages) - 1
        if stage.step > 0:
            rhs = AHDL_VAR(v_prev, Ctx.LOAD)
        else:
            rhs = AHDL_CONST(1)
        set_valid = AHDL_MOVE(AHDL_VAR(v_now, Ctx.STORE), rhs)
        stage.codes.append(set_valid)

        if is_last:
            v_next = pstate.valid_signal(stage.step + 1)
            set_valid = AHDL_MOVE(AHDL_VAR(v_next, Ctx.STORE),
                                  AHDL_VAR(v_now, Ctx.LOAD))
            stage.codes.append(set_valid)


class WorkerPipelineStageBuilder(PipelineStageBuilder):
    def __init__(self, scope, stg, blk2states):
        super().__init__(scope, stg, blk2states)
        self.is_finite_loop = False

    def _build_scheduled_items(self, dfg):
        nodes = []
        for n in dfg.get_scheduled_nodes():
            if n.begin < 0:
                continue
            nodes.append(n)
        super()._build_scheduled_items(nodes)