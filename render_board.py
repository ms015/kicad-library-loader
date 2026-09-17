"""Run only with KiCad's bundled Python to build a small preview board."""
import json
from pathlib import Path
import sys


def main():
    import pcbnew

    job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    board = pcbnew.BOARD()
    fp = pcbnew.FootprintLoad(job['library'], job['footprint'])
    if fp is None:
        raise ValueError('フットプリントを読み込めません')
    fp.SetReference('U1')
    fp.SetPosition(pcbnew.VECTOR2I(0, 0))
    board.Add(fp)
    bounds = fp.GetBoundingBox(False, False)
    margin = pcbnew.FromMM(1.2)
    x0, y0 = bounds.GetX() - margin, bounds.GetY() - margin
    x1, y1 = bounds.GetRight() + margin, bounds.GetBottom() + margin
    for a, b in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
        line = pcbnew.PCB_SHAPE()
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetStart(pcbnew.VECTOR2I(*a))
        line.SetEnd(pcbnew.VECTOR2I(*b))
        line.SetLayer(pcbnew.Edge_Cuts)
        line.SetWidth(pcbnew.FromMM(0.05))
        board.Add(line)
    pcbnew.SaveBoard(job['board'], board)


if __name__ == '__main__':
    main()
