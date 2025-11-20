import struct
import sys
import json

def load_trace(fname, MAX_LOAD=5000, LOAD_OFF=0):
    with open(fname, 'rb') as f:
        trace = f.read()

    ver, nr_rec = struct.unpack('iI', trace[:8])
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

def dump_ms_csv(records):
    print("tid,cid,ms,us")
    for rec in records:
        print("%d,%d,%d,%d" % (rec[0],rec[1], rec[2]//1000, rec[2]%1000))

def to_echarts_series(records, name=None):
    obj = {
        "type": "scatter",
        "symbolSize": 2,
        "data": [ (rec[2]//1000, rec[2]%1000) for rec in records ]
    }
    if name is not None: obj['name'] = name
    return obj

series = []
for fname in sys.argv[1:]:
    records = load_trace(fname)
    s = to_echarts_series(records, fname)
    series.append(s)

print(json.dumps(series))
