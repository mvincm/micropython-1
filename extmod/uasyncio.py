"""
MicroPython uasyncio module
MIT license; Copyright (c) 2019 Damien P. George
"""

from time import ticks_ms as ticks
import select

################################################################################
# Linked list functions

def _q_pop_head(q):
    global _q_last
    v = q[0]
    q[0] = v.next
    if _q_last is v:
        _q_last = v.next
    return v

_q_last = None
def _q_push_sorted(q, v, data):
    v.data = data

    global _q_last
    if q is _queue and data <= ticks():
        cur = _q_last
        if cur and data >= cur.data:
            # Optimisation: can start looking from _q_last to insert this item
            while cur.next and data >= cur.next.data:
                cur = cur.next
            v.next = cur.next
            cur.next = v
            _q_last = cur
            return

    if not q[0] or (isinstance(q[0].data, int) and data < q[0].data):
        v.next = q[0]
        q[0] = v
    else:
        cur = q[0]
        while cur.next and (not isinstance(q[0].data, int) or data >= cur.next.data):
            cur = cur.next
        v.next = cur.next
        cur.next = v

        if q is _queue:
            _q_last = cur

def _q_push_head(q, v):
    _q_push_sorted(q, v, ticks())

def _q_push_error(q, v, err):
    # Push directly to head (but should probably still consider fairness)
    v.data = err
    v.next = q[0]
    q[0] = v

def _q_remove_from(q, v):
    global _q_last
    if q[0] is None:
        pass
    elif q[0] is v:
        q[0] = v.next
    else:
        cur = q[0]
        while cur.next:
            if cur.next is v:
                cur.next = v.next
                break
            cur = cur.next
    if _q_last is v:
        _q_last = v.next

################################################################################
# Fundamental classes

class CancelledError(BaseException):
    pass

class TimeoutError(Exception):
    pass

# Event class for primitive events that can be waited on, set, and cleared
class Event:
    def __init__(self):
        self.waiting = [None] # Linked list of Tasks waiting on completion of this event
    def set(self):
        if self.waiting:
            # Event becomes set, schedule any tasks waiting on it
            while self.waiting[0]:
                _q_push_head(_queue, _q_pop_head(self.waiting))
            self.waiting = None # Indicate event is now set
    def clear(self):
        if not self.waiting:
            self.waiting = [None]
    async def wait(self):
        if self.waiting:
            # Event not set, put the calling task on the event's waiting queue
            _q_push_head(self.waiting, cur_task)
            # Set calling task's data to this event that it waits on, to double-link it
            cur_task.data = self
            try:
                yield
            except CancelledError as er:
                if self.waiting:
                    _q_remove_from(self.waiting, cur_task)
                raise er
        return True

# Task class representing a coroutine, can be waited on and cancelled
class Task:
    def __init__(self, coro):
        self.coro = coro # Coroutine of this Task
        self.next = None # For linked list
        self.data = None # General data for linked list
    def __iter__(self):
        if not hasattr(self, 'waiting'):
            # Lazily allocated head of linked list of Tasks waiting on completion of this task
            self.waiting = [None]
        return self
    def send(self, v):
        if not self.coro:
            # Task finished, raise return value to caller so it can continue
            raise self.data
        else:
            # Put calling task on waiting queue
            _q_push_head(self.waiting, cur_task)
            # Set calling task's data to this task that it waits on, to double-link it
            cur_task.data = self
    def cancel(self):
        # If Task waits on another task then forward the cancel to the one it's waiting on
        while isinstance(self.data, Task):
            self = self.data
        # Reschedule Task as a cancelled task
        if isinstance(self.data, Event):
            _q_remove_from(self.data.waiting, self)
        else:
            _q_remove_from(_queue, self)
        _q_push_error(_queue, self, CancelledError)
        return True

# Create and schedule a new task from a coroutine
def create_task(coro):
    t = Task(coro)
    _q_push_head(_queue, t)
    return t

# "Yield" once, then raise StopIteration
class SingletonGenerator:
    def __init__(self):
        self.state = 0
        self.exc = StopIteration()
    def __iter__(self):
        return self
    def __next__(self):
        if self.state:
            self.state = 0
            return None
        else:
            self.exc.__traceback__ = None
            raise self.exc

# Pause task execution for the given time (integer in milliseconds, uPy extension)
# Use a SingletonGenerator to do it without allocating on the heap
def sleep_ms(t, sgen=SingletonGenerator()):
    _q_push_sorted(_queue, cur_task, ticks() + t)
    sgen.state = 1
    return sgen

# Pause task execution for the given time (in seconds)
def sleep(t):
    return sleep_ms(int(t * 1000))

################################################################################
# Helper functions

def _promote_to_task(aw):
    return aw if isinstance(aw, Task) else create_task(aw)

def run(coro):
    return run_until_complete(create_task(coro))

async def wait_for(aw, timeout):
    aw = _promote_to_task(aw)
    if timeout is None:
        return await aw
    def cancel(aw, timeout):
        await sleep(timeout)
        aw.cancel()
    cancel_task = create_task(cancel(aw, timeout))
    try:
        ret = await aw
    except CancelledError:
        # Ignore CancelledError from aw, it's probably due to timeout
        pass
    finally:
        _q_remove_from(_queue, cancel_task)
    if cancel_task.coro is None:
        # Cancel task ran to completion, ie there was a timeout
        raise TimeoutError
    return ret

async def gather(*aws, return_exceptions=False):
    ts = [_promote_to_task(aw) for aw in aws]
    for i in range(len(ts)):
        try:
            # TODO handle cancel of gather itself
            #if ts[i].coro:
            #    _q_push_head(iter(ts[i]).waiting, cur_task)
            #    try:
            #        yield
            #    except CancelledError as er:
            #        # cancel all waiting tasks
            #        raise er
            ts[i] = await ts[i]
        except Exception as er:
            if return_exceptions:
                ts[i] = er
            else:
                raise er
    return ts

################################################################################
# General streams

# Queue and poller for stream IO
class IOQueue:
    def __init__(self):
        self.poller = select.poll()
        self.map = {}
    def _queue(self, s, idx):
        if id(s) not in self.map:
            entry = [None, None, s]
            entry[idx] = cur_task
            self.map[id(s)] = entry
            self.poller.register(s, select.POLLIN if idx == 0 else select.POLLOUT)
        else:
            sm = self.map[id(s)]
            assert sm[idx] is None
            assert sm[1 - idx] is not None
            sm[idx] = cur_task
            self.poller.modify(s, select.POLLIN | select.POLLOUT)
    def _dequeue(self, s):
        del self.map[id(s)]
        self.poller.unregister(s)
    def queue_read(self, s):
        self._queue(s, 0)
    def queue_write(self, s):
        self._queue(s, 1)
    def remove(self, task):
        while True:
            del_s = None
            for k in self.map: # Iterate without allocating on the heap
                q0, q1, s = self.map[k]
                if q0 is task or q1 is task:
                    del_s = s
                    break
            if del_s is not None:
                self._dequeue(s)
            else:
                break
    def wait_io_event(self, dt):
        for s, ev in self.poller.ipoll(dt):
            sm = self.map[id(s)]
            err = ev & ~(select.POLLIN | select.POLLOUT)
            #print('poll', s, sm, ev, err)
            if ev & select.POLLIN or (err and sm[0] is not None):
                _q_push_head(_queue, sm[0])
                sm[0] = None
            if ev & select.POLLOUT or (err and sm[1] is not None):
                _q_push_head(_queue, sm[1])
                sm[1] = None
            if sm[0] is None and sm[1] is None:
                self._dequeue(s)
            elif sm[0] is None:
                self.poller.modify(s, select.POLLOUT)
            else:
                self.poller.modify(s, select.POLLIN)

class Stream:
    def __init__(self, s, e={}):
        self.s = s
        self.e = e
        self.out_buf = b''
    def get_extra_info(self, v):
        return self.e[v]
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
    def close(self):
        pass
    async def wait_closed(self):
        # TODO yield?
        self.s.close()
    async def read(self, n):
        _io_queue.queue_read(self.s)
        yield
        return self.s.read(n)
    async def readline(self):
        l = b''
        while True:
            _io_queue.queue_read(self.s)
            yield
            l2 = self.s.readline() # may do multiple reads but won't block
            l += l2
            if not l2 or l[-1] == 10: # \n (check l in case l2 is str)
                return l
    def write(self, buf):
        self.out_buf += buf
    async def drain(self):
        mv = memoryview(self.out_buf)
        off = 0
        while off < len(mv):
            _io_queue.queue_write(self.s)
            yield
            ret = self.s.write(mv[off:])
            if ret is not None:
                off += ret
        self.out_buf = b''

################################################################################
# Socket streams

# Create a TCP stream connection to a remove host
async def open_connection(host, port):
    try:
        import usocket as socket
    except ImportError:
        import socket
    ai = socket.getaddrinfo(host, port)[0] # TODO this is blocking!
    s = socket.socket()
    s.setblocking(False)
    ss = Stream(s)
    try:
        s.connect(ai[-1])
    except OSError as er:
        if er.args[0] != 115: # EINPROGRESS
            raise er
    _io_queue.queue_write(s)
    yield
    return ss, ss

# Class representing a TCP stream server, can be closed and used in "async with"
class Server:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        self.close()
        await self.wait_closed()
    def close(self):
        self.task.cancel()
    async def wait_closed(self):
        await self.task
    async def _serve(self, cb, host, port, backlog):
        try:
            import usocket as socket
        except ImportError:
            import socket
        ai = socket.getaddrinfo(host, port)[0] # TODO this is blocking!
        s = socket.socket()
        s.setblocking(False)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(ai[-1])
        s.listen(backlog)
        self.task = cur_task
        # Accept incoming connections
        while True:
            _io_queue.queue_read(s)
            try:
                yield
            except CancelledError:
                # Shutdown server
                s.close()
                return
            s2, addr = s.accept()
            s2.setblocking(False)
            s2s = Stream(s2, {'peername': addr})
            create_task(cb(s2s, s2s))

# Helper function to start a TCP stream server, running as a new task
# TODO could use an accept-callback on socket read activity instead of creating a task
async def start_server(cb, host, port, backlog=5):
    s = Server()
    create_task(s._serve(cb, host, port, backlog))
    return s

################################################################################
# Main run loop

# Linked list of Task instances
_queue = [None]

# Task queue and poller for stream IO
_io_queue = IOQueue()

# Keep scheduling tasks until there are none left to schedule
def run_until_complete(main_task=None):
    global cur_task
    excs = (CancelledError, Exception) # To prevent heap allocation in loop
    while True:
        # Wait until the head of _queue is ready to run
        dt = 1
        while dt > 0:
            dt = -1
            if _queue[0]:
                # A task waiting on _queue
                if isinstance(_queue[0].data, type):
                    # "data" is an exception to throw into the task
                    dt = 0
                else:
                    # "data" is time to schedule task at
                    dt = max(0, _queue[0].data - ticks())
            elif not _io_queue.map:
                # No tasks can be woken so finished running
                return
            #print('(poll {})'.format(dt), len(_io_queue.map))
            _io_queue.wait_io_event(dt)

        # Get next task to run and continue it
        t = _q_pop_head(_queue)
        cur_task = t
        try:
            # Continue running the coroutine, it's responsible for rescheduling itself
            if isinstance(t.data, type):
                t.coro.throw(t.data)
            else:
                t.coro.send(None)
        except excs as er:
            # This task is done, schedule any tasks waiting on it
            if t is main_task:
                # TODO this assumes StopIteration
                return er.value
            t.data = er # save return value of coro to pass up to caller
            if hasattr(t, 'waiting'):
                while t.waiting[0]:
                    _q_push_head(_queue, _q_pop_head(t.waiting))
                t.waiting = None # Free waiting queue head
            _io_queue.remove(t) # Remove task from the IO queue (if it's on it)
            t.coro = None # Indicate task is done

################################################################################
# Legacy uasyncio compatibility

async def stream_awrite(self, buf, off=0, sz=-1):
    if off != 0 or sz != -1:
        buf = memoryview(buf)
        if sz == -1:
            sz = len(buf)
        buf = buf[off:off + sz]
    self.write(buf)
    await self.drain()

Stream.aclose = Stream.wait_closed
Stream.awrite = stream_awrite
Stream.awritestr = stream_awrite # TODO explicitly convert to bytes?

StreamReader = Stream
StreamWriter = Stream

class Loop:
    def create_task(self, coro):
        return create_task(coro)
    def run_forever(self):
        run_until_complete()
        # TODO should keep running until .stop() is called, even if there're no tasks left
    def run_until_complete(self, aw):
        return run_until_complete(_promote_to_task(aw))
    def close(self):
        pass

def get_event_loop(n=0, m=0):
    return Loop()
