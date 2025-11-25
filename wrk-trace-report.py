import struct
import sys
import json
import argparse

FILT_CIDS = None
SYM_SIZE = 2

MAX_LOAD=5000
LOAD_OFF=0
def load_trace(fname):
    with open(fname, 'rb') as f:
        trace = f.read()

    ver, nr_rec = struct.unpack('iI', trace[:8])
    assert ver == 1, "unknown trace version: %s" % ver

    body = trace[8:]
    if LOAD_OFF > 0:
        assert LOAD_OFF < nr_rec
        body = body[8*LOAD_OFF:]
        nr_rec -= LOAD_OFF
    if nr_rec > MAX_LOAD:
        nr_rec = MAX_LOAD

    records = []
    for i in range(nr_rec):
        rec = body[i*8:i*8+8]
        # tid, cid, us
        records.append(struct.unpack('HHI', rec))
    return records


def dump_csv(records):
    print("tid,cid,us")
    for rec in records:
        print("%d,%d,%d" % rec)

def report_csv(args):
    for fname in args.file:
        records = load_trace(fname)
        dump_csv(records)

def dump_ms_csv(records):
    print("tid,cid,ms,us")
    for rec in records:
        print("%d,%d,%d,%d" % (rec[0],rec[1], rec[2]//1000, rec[2]%1000))

def report_ms_csv(args):
    for fname in args.file:
        records = load_trace(fname)
        dump_ms_csv(records)

def expand_lists(s):
    el = []
    for l in s.split(','):
        if '-' in l:
            a, b = l.split('-')
            el.extend(range(int(a), int(b)+1))
        else:
            el.append(int(l))
    return el

def to_echarts_series(records, name=None, cids=None):
    obj = {
        "type": "scatter",
        "symbolSize": SYM_SIZE,
        "data": [ (rec[2]//1000, rec[2]%1000) for rec in records if not cids or rec[1] in cids ]
    }
    if name is not None: obj['name'] = name
    return [obj]

def to_echarts_series_by_cid(records, name, cids):
    objs = []
    for cid in cids:
        obj = {
            "type": "scatter",
            "symbolSize": SYM_SIZE,
            "name": name + " conn%d" % cid,
            "data": [ (rec[2]//1000, rec[2]%1000) for rec in records if rec[1] == cid ]
        }
        objs.append(obj)
    return objs

def report_echart(args):
    series = []
    for fname in args.file:
        records = load_trace(fname)
        if args.by_cid:
            s = to_echarts_series_by_cid(records, fname, cids=(FILT_CIDS if FILT_CIDS else range(10)))
        else:
            s = to_echarts_series(records, fname, cids=FILT_CIDS)
        series.extend(s)
    print(json.dumps(series))


def event(name, cat, ph, ts, **kwargs):
    ev = {
        "name": str(name),
        "cat": cat,
        "ph": ph,
        "ts": ts,
    }
    ev.update(kwargs)
    return ev

def meta_event(prop, pid, **kwargs):
    tid = kwargs.pop('tid', None)
    ev = {
        "name": prop,
        "cat": '__metadata',
        "ph": 'M',
        "ts": 0,
        "pid": pid,
        "args": kwargs
    }
    if tid is not None:
        ev['tid'] = tid
    return ev

# assume #conn < 100000 per thread, fake up ids so main thread can display correctly
EVPID = lambda tid: tid * 100000
EVTID = lambda tid, cid: EVPID(tid) + cid

def report_traceevent(args):
    events = []
    for fname in args.file:
        records = load_trace(fname)
        tid = records[0][0]
        cids = set([rec[1] for rec in records])
        metas = [
            meta_event('process_name', EVPID(tid), name='thread-%d (%s)' % (tid, fname)),
            meta_event('thread_name', EVPID(tid), tid=EVTID(tid, 0), name='Event-loop-%d' % tid)
        ]
        metas.extend([
            meta_event('thread_name', EVPID(tid), tid=EVTID(tid, 1+cid), name='conn-%d' % cid) for cid in cids
        ])

        evs = [event('aeMain', 'PERF', 'B', 0, pid=EVPID(tid), tid=EVTID(tid, 0))]
        conn = None
        for rec in records:
            if conn:
                evs.append(event(str(conn[1]), 'PERF', 'E', rec[2], pid=EVPID(tid), tid=EVTID(tid, 0)))
            evs.append(event(str(rec[1]), 'PERF', 'B', rec[2], pid=EVPID(tid), tid=EVTID(tid, 0)))
            conn = rec

            evs.append(event('Req', 'PERF', 'i', rec[2], pid=EVPID(rec[0]), tid=EVTID(rec[0], 1+rec[1])))

        events.extend(metas)
        events.extend(evs)

    json.dump({ 'traceEvents': events }, sys.stdout)


parser = argparse.ArgumentParser()
parser.add_argument('-n', action="store", type=int)
parser.add_argument('-o', action="store", type=int)
parser.add_argument('-S', action="store", type=int)
parser.add_argument('-O', choices=["csv", "ms_csv", "echart", "traceevent"], default="echart")
parser.add_argument('--by-cid', action="store_true")
parser.add_argument('--cids', action="store")
parser.add_argument('file', nargs='+')
args = parser.parse_args()

if args.n: MAX_LOAD = args.n
if args.o: LOAD_OFF = args.o
if args.S: SYM_SIZE = args.S
if args.cids: FILT_CIDS = expand_lists(args.cids)

if args.O == "csv":
    report_csv(args)
elif args.O == "ms_csv":
    report_ms_csv(args)
elif args.O == "echart":
    report_echart(args)
elif args.O == "traceevent":
    report_traceevent(args)
else:
    raise Exception("unknown report format:" + repr(args.O))

