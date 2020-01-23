"""
MicroPython uasyncio module
MIT license; Copyright (c) 2019 Damien P. George
"""

from time import ticks_ms as ticks, ticks_diff, ticks_add
import sys, select

################################################################################
# Queue class based on a pairing heap

# O(1)
def ph_meld(h1, h2):
    if h1 is None:
        return h2
    if h2 is None:
        return h1
    lt = ticks_diff(h1.ph_key, h2.ph_key) < 0
    if lt:
        if h1.ph_child is None:
            h1.ph_child = h2
        else:
            h1.ph_child_last.ph_next = h2
        h1.ph_child_last = h2
        h2.ph_next = None
        h2.ph_rightmost_parent = h1
        return h1
    else:
        h1.ph_next = h2.ph_child
        h2.ph_child = h1
        if h1.ph_next is None:
            h2.ph_child_last = h1
            h1.ph_rightmost_parent = h2
        return h2

# amortised O(log N)
def ph_pairing(child):
    heap = None
    while child is not None:
        n1 = child
        child = child.ph_next
        if child is not None:
            n2 = child
            child = child.ph_next
            n1 = ph_meld(n1, n2)
        heap = ph_meld(heap, n1)
    return heap

# stable, amortised O(log N)
def ph_delete(heap, node):
    if node is heap:
        return ph_pairing(heap.ph_child)
    # Find parent of node
    parent = node
    while parent.ph_next is not None:
        parent = parent.ph_next
    parent = parent.ph_rightmost_parent
    # Replace node with pairing of its children
    if node is parent.ph_child and node.ph_child is None:
        parent.ph_child = node.ph_next
        return heap
    elif node is parent.ph_child:
        next = node.ph_next
        node = ph_pairing(node.ph_child)
        parent.ph_child = node
    else:
        n = parent.ph_child
        while node is not n.ph_next:
            n = n.ph_next
        next = node.ph_next
        node = ph_pairing(node.ph_child)
        if node is None:
            node = n
        else:
            n.ph_next = node
    node.ph_next = next
    if next is None:
        node.ph_rightmost_parent = parent
        parent.ph_child_last = node
    return heap

class Queue:
    def __init__(self):
        self.heap = None

    def peek(self):
        return self.heap

    def push_sorted(self, v, data):
        v.ph_key = data
        v.ph_child = None
        v.ph_next = None
        self.heap = ph_meld(v, self.heap)

    def push_head(self, v):
        v.data = None
        self.push_sorted(v, ticks())

    def pop_head(self):
        v = self.heap
        self.heap = ph_pairing(self.heap.ph_child)
        return v

    def remove(self, v):
        self.heap = ph_delete(self.heap, v)

################################################################################
# Fundamental classes

class CancelledError(BaseException):
    pass

class TimeoutError(Exception):
    pass

# Task class representing a coroutine, can be waited on and cancelled
class Task:
    def __init__(self, coro):
        self.coro = coro # Coroutine of this Task
        self.data = None # General data for queue it is waiting on
        self.ph_key = 0 # Pairing heap
        self.ph_child = None # Paring heap
        self.ph_child_last = None # Paring heap
        self.ph_next = None # Paring heap
        self.ph_rightmost_parent = None # Paring heap
    def __iter__(self):
        if not hasattr(self, 'waiting'):
            # Lazily allocated head of linked list of Tasks waiting on completion of this task
            self.waiting = Queue()
        return self
    def __next__(self):
        if not self.coro:
            # Task finished, raise return value to caller so it can continue
            raise self.data
        else:
            # Put calling task on waiting queue
            self.waiting.push_head(cur_task)
            # Set calling task's data to this task that it waits on, to double-link it
            cur_task.data = self
    def cancel(self):
        # Check if task is already finished
        if self.coro is None:
            return False
        # Can't cancel self (not supported yet)
        if self is cur_task:
            raise RuntimeError('cannot cancel self')
        # If Task waits on another task then forward the cancel to the one it's waiting on
        while isinstance(self.data, Task):
            self = self.data
        # Reschedule Task as a cancelled task
        if hasattr(self.data, 'remove'):
            # Not on the main running queue, remove the task from the queue it's on
            self.data.remove(self)
            _queue.push_head(self)
        elif ticks_diff(self.ph_key, ticks()) > 0:
            # On the main running queue but scheduled in the future, so bring it forward to now
            _queue.remove(self)
            _queue.push_head(self)
        self.data = CancelledError
        return True

# Create and schedule a new task from a coroutine
def create_task(coro):
    t = Task(coro)
    _queue.push_head(t)
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
    _queue.push_sorted(cur_task, ticks_add(ticks(), t))
    cur_task.data = None
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
        # Cancel the "cancel" task if it's still active (optimisation instead of cancel_task.cancel())
        if cancel_task.coro is not None:
            _queue.remove(cancel_task)
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
            #    iter(ts[i]).waiting.push_head(cur_task)
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
        self.map = {} # maps id(stream) to [task_waiting_read, task_waiting_write, stream]
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
        # Link task to this IOQueue so it can be removed if needed
        cur_task.data = self
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
            #print('poll', s, sm, ev)
            if ev & ~select.POLLOUT and sm[0] is not None:
                # POLLIN or error
                _queue.push_head(sm[0])
                sm[0] = None
            if ev & ~select.POLLIN and sm[1] is not None:
                # POLLOUT or error
                _queue.push_head(sm[1])
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
        yield _io_queue.queue_read(self.s)
        return self.s.read(n)
    async def readline(self):
        l = b''
        while True:
            yield _io_queue.queue_read(self.s)
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
            yield _io_queue.queue_write(self.s)
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
    yield _io_queue.queue_write(s)
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
            try:
                yield _io_queue.queue_read(s)
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

# Queue of Task instances
_queue = Queue()

# Task queue and poller for stream IO
_io_queue = IOQueue()

# Keep scheduling tasks until there are none left to schedule
def run_until_complete(main_task=None):
    global cur_task
    excs_all = (CancelledError, Exception) # To prevent heap allocation in loop
    excs_stop = (CancelledError, StopIteration) # To prevent heap allocation in loop
    while True:
        # Wait until the head of _queue is ready to run
        dt = 1
        while dt > 0:
            dt = -1
            t = _queue.peek()
            if t:
                # A task waiting on _queue; "ph_key" is time to schedule task at
                dt = max(0, ticks_diff(t.ph_key, ticks()))
            elif not _io_queue.map:
                # No tasks can be woken so finished running
                return
            #print('(poll {})'.format(dt), len(_io_queue.map))
            _io_queue.wait_io_event(dt)

        # Get next task to run and continue it
        t = _queue.pop_head()
        cur_task = t
        try:
            # Continue running the coroutine, it's responsible for rescheduling itself
            if not t.data:
                t.coro.send(None)
            else:
                t.coro.throw(t.data)
        except excs_all as er:
            # This task is done, check if it's the main task and then loop should stop
            if t is main_task:
                if isinstance(er, StopIteration):
                    return er.value
                raise er
            # Save return value of coro to pass up to caller
            t.data = er
            # Schedule any other tasks waiting on the completion of this task
            waiting = False
            if hasattr(t, 'waiting'):
                while t.waiting.peek():
                    _queue.push_head(t.waiting.pop_head())
                    waiting = True
                t.waiting = None # Free waiting queue head
            # Indicate task is done
            t.coro = None
            # Print out exception for detached tasks
            if not waiting and not isinstance(er, excs_stop):
                print('task raised exception:', t.coro)
                sys.print_exception(er)

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

def get_event_loop(runq_len=0, waitq_len=0):
    return Loop()
