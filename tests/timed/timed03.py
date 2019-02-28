from polyphony import module, timed
from polyphony import testbench
from polyphony import Reg
from polyphony.io import Port
from polyphony.timing import clkfence


@timed
@module
class timed03:
    def __init__(self):
        self.i = Port(int, 'in')
        self.o = Port(int, 'out')
        self.append_worker(self.w)

    def w(self):
        y = Reg()
        z = Reg()

        clkfence()

        x = self.i.rd()
        assert 3 == x
        clkfence()

        y.v = self.i.rd()
        assert y.v == 0
        clkfence()

        z.v = self.i.rd()
        assert z.v == 0
        clkfence()

        self.o.wr(y.v + z.v)
        clkfence()


@timed
@testbench
def test(m):
    m.i.wr(3)
    clkfence()

    m.i.wr(4)
    clkfence()

    m.i.wr(5)
    clkfence()

    # m read z
    clkfence()

    # m write to o
    clkfence()

    #print(m.o.rd())
    assert 4 + 5 == m.o.rd()


m = timed03()
test(m)
