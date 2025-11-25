import struct
import sys
import json
import argparse

class Event():
    def __init__(self, name, value):
        self.name = name
        self.value = value
    def __str__(self):
        return self.name

# NOTE: async connect() will trigger epoll repeatedly once connected
#
# https://man7.org/linux/man-pages/man3/connect.3p.html
#       If the connection cannot be established immediately and O_NONBLOCK
#       is set for the file descriptor for the socket, connect() shall
#       fail and set errno to [EINPROGRESS], but the connection request
#       shall not be aborted, and the connection shall be established
#       asynchronously. Subsequent calls to connect() for the same socket,
#       before the connection is established, shall fail and set errno to
#       [EALREADY].
#
#       When the connection has been established asynchronously,
#       pselect(), select(), and poll() shall indicate that the file
#       descriptor for the socket is ready for writing.

class Record():
    EVENTS = [
        Event("REQ", 0),
        Event("RESP", 1),
        Event("CONN_START", 2),
        Event("CONNECTED", 3),
        Event("LOOP_START", 4),
        Event("EPOLL_WAIT", 5),
        Event("EPOLL_WAKE", 6),
        Event("DELAY_REQ_FE",  7),
        Event("DELAY_REQ_TE",  8),
    ]
    EVENT = {ev.name: ev.value for ev in EVENTS}
    def __init__(self, tid, event, cid, us):
        self.tid = tid
        self.event = event
        self.cid = cid
        self.us = us
    def __repr__(self):
        return repr(self.__dict__)


MAX_LOAD=5000
LOAD_OFF=0
def load_trace(fname):
    with open(fname, 'rb') as f:
        trace = f.read()

    ver, nr_rec = struct.unpack('iI', trace[:8])
    assert ver in [1, 2], "unknown trace version: %s" % ver

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
        if ver == 1:
            tid, cid, us = struct.unpack('HHI', rec)
            event = 0
        elif ver == 2:
            tid, event, cid, us = struct.unpack('BBHI', rec)
        records.append(Record(tid, event, cid, us))
    return ver, records


FILT_CIDS = None
SYM_SIZE = 2

def dump_csv(records):
    print("tid,cid,us,event")
    for rec in records:
        print("%d,%d,%d,%s" % (rec.tid, rec.cid, rec.us, Record.EVENTS[rec.event]))

def report_csv(args):
    for fname in args.file:
        ver, records = load_trace(fname)
        dump_csv(records)

def dump_ms_csv(records):
    print("tid,cid,ms,us,event")
    for rec in records:
        print("%d,%d,%d,%d,%s" % (rec.tid, rec.cid, rec.us//1000, rec.us%1000, Record.EVENTS[rec.event]))

def report_ms_csv(args):
    for fname in args.file:
        ver, records = load_trace(fname)
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
        "data": [ (rec.us//1000, rec.us%1000) for rec in records if (rec.event == Record.EVENT["REQ"]) and (not cids or rec.cid in cids) ]
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
            "data": [ (rec.us//1000, rec.us%1000) for rec in records if (rec.event == Record.EVENT["REQ"]) and  rec.cid == cid ]
        }
        objs.append(obj)
    return objs

def report_echart(args):
    series = []
    for fname in args.file:
        ver, records = load_trace(fname)
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

def to_traceevent_v1(records):
    tid = records[0].tid
    evs = [event('aeMain', 'loop', 'B', 0, pid=EVPID(tid), tid=EVTID(tid, 0))]
    conn = None
    for rec in records:
        if conn:
            evs.append(event(str(conn.cid), 'loop', 'E', rec.us, pid=EVPID(tid), tid=EVTID(tid, 0)))
        evs.append(event(str(rec.cid), 'loop', 'B', rec.us, pid=EVPID(tid), tid=EVTID(tid, 0)))
        conn = rec

        evs.append(event('Req', 'conn', 'i', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
    return evs

def to_traceevent_v2(records):
    rec0 = records[0]
    assert rec0.event == Record.EVENT["LOOP_START"], "first trace record not LOOP_START"
    tid = rec0.tid
    evs = [event('aeMain', 'loop', 'B', rec0.us, pid=EVPID(tid), tid=EVTID(tid, 0))]

    ep_wait_to = None

    for rec in records[1:]:
        # conn events
        if rec.event == Record.EVENT["REQ"]:
            evs.append(event('Req', 'conn', 'B', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        elif rec.event == Record.EVENT["RESP"]:
            evs.append(event('Req', 'conn', 'E', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        elif rec.event == Record.EVENT["CONN_START"]:
            evs.append(event('Connect', 'conn', 'B', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        elif rec.event == Record.EVENT["CONNECTED"]:
            evs.append(event('Connect', 'conn', 'E', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        elif rec.event == Record.EVENT["DELAY_REQ_FE"]: # triggered by RESP
            evs.append(event('Delay(FileEvent)', 'conn', 'i', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        # loop events
        elif rec.event == Record.EVENT["DELAY_REQ_TE"]: # triggered by loop now_ms >= te->when_ms
            evs.append(event('Delay(TimeEvent)', 'loop', 'i', rec.us, pid=EVPID(tid), tid=EVTID(tid, 1+rec.cid)))
        elif rec.event == Record.EVENT["EPOLL_WAIT"]:
            ep_wait_to = rec.cid
            evs.append(event('epoll_wait(%d)' % ep_wait_to, 'PERF', 'B', rec.us, pid=EVPID(tid), tid=EVTID(tid, 0)))
        elif rec.event == Record.EVENT["EPOLL_WAKE"]:
            # rec.cid: numevents
            evs.append(event('epoll_wait(%d)' % ep_wait_to, 'PERF', 'E', rec.us, pid=EVPID(tid), tid=EVTID(tid, 0)))
            ep_wait_to = None

    return evs

def to_traceevent(fname):
    ver, records = load_trace(fname)

    tid = records[0].tid
    cids = set([rec.cid for rec in records if rec.event == Record.EVENT["REQ"]])
    events = [
        meta_event('process_name', EVPID(tid), name='thread-%d (%s)' % (tid, fname)),
        meta_event('thread_name', EVPID(tid), tid=EVTID(tid, 0), name='Event-loop-%d' % tid)
    ]
    events.extend([
        meta_event('thread_name', EVPID(tid), tid=EVTID(tid, 1+cid), name='conn-%d' % cid) for cid in cids
    ])

    if ver == 1:
        events += to_traceevent_v1(records)
    elif ver == 2:
        events += to_traceevent_v2(records)
    return events

def report_traceevent(args):
    events = []
    for fname in args.file:
        events += to_traceevent(fname)
    json.dump({ 'traceEvents': events }, sys.stdout)


parser = argparse.ArgumentParser()
parser.add_argument('-n', action="store", type=int)
parser.add_argument('-o', action="store", type=int)
parser.add_argument('-S', action="store", type=int)
parser.add_argument('-O', choices=["csv", "ms_csv", "echart", "traceevent"], default="traceevent")
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

