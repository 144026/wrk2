#ifndef TRACE_H
#define TRACE_H

#include <assert.h>
#include <unistd.h>
#include <fcntl.h>
#include "wrk.h"

enum trace_event {
    TRACE_EV_REQ = 0,
    TRACE_EV_RESP,
    TRACE_EV_CONN_START,
    TRACE_EV_CONNECTED,
    TRACE_EV_LOOP_START,
    TRACE_EV_EPOLL_WAIT,
    TRACE_EV_EPOLL_WAKE,
    TRACE_EV_DELAY_REQ_FE,
    TRACE_EV_DELAY_REQ_TE,
    TRACE_EV_EXPECT_REQ_FE,
    TRACE_EV_EXPECT_REQ_TE, // Unused
};

struct trace_record {
    uint8_t tid;
    uint8_t event;
    uint16_t cid;
    uint32_t us;
};

static inline void trace_sock_write(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_REQ;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_resp(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_RESP;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_conn_start(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_CONN_START;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_connected(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_CONNECTED;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_loop_start(thread* t, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_LOOP_START;
        rec->cid = 0;
        rec->us = us;
    }
}

static inline void trace_epoll_wait(thread* t, int timeout, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_EPOLL_WAIT;
        rec->cid = timeout;
        rec->us = us;
    }
}

static inline void trace_epoll_wake(thread* t, int ep_ret, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_EPOLL_WAKE;
        rec->cid = ep_ret;
        rec->us = us;
    }
}

static inline void trace_sock_delay_req_fe(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_DELAY_REQ_FE;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_delay_req_te(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_DELAY_REQ_TE;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_expect_req_fe(thread* t, int cid, uint64_t us)
{
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_EXPECT_REQ_FE;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void trace_sock_expect_req_te(thread* t, int cid, uint64_t us)
{ // Unused
    if (t->trace_idx < t->trace_max_idx) {
        struct trace_record *rec = (void *)&t->trace_buf[t->trace_idx++];
        rec->tid = t->id;
        rec->event = TRACE_EV_EXPECT_REQ_TE;
        rec->cid = cid;
        rec->us = us;
    }
}

static inline void open_trace_sock(thread *t)
{
    static const uint64_t bufsize = 4096U * 4096U;
    t->trace_idx = 0;
    /* 16MB per thread */
    t->trace_max_idx = bufsize / sizeof(*t->trace_buf);
    t->trace_buf = zcalloc(bufsize);
    assert(t->trace_buf);
}

static inline void sync_trace_sock(thread *t, uint32_t start_us)
{
    int i;

    for (i = 0; i < t->trace_idx; i++) {
        struct trace_record *rec = (void *)&t->trace_buf[i];

        rec->us -= start_us;
    }
}

static inline void dump_trace_sock(thread *t)
{
    char trace_path[128];
    int fd, flags = O_RDWR|O_CREAT|O_EXCL;

    snprintf(trace_path, sizeof trace_path, "wrk-thread%d.trace", t->id);
again:
    fd = open(trace_path, flags, 0666);
    if (fd < 0) {
        if (errno == EEXIST) {
            flags = O_RDWR;
            goto again;
        }
        fprintf(stderr, "%s: %s\n", trace_path, strerror(errno));
        return;
    }

    struct trace_head {
        int version;
        uint32_t nr_rec;
    } head = { 2, t->trace_idx };

    write(fd, &head, sizeof head);
    write(fd, t->trace_buf, t->trace_idx * sizeof(*t->trace_buf));

    close(fd);
}

#endif
