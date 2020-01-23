# MicroPython uasyncio module
# MIT license; Copyright (c) 2019-2020 Damien P. George

from . import core

# Lock class for primitive mutex capability
class Lock:
    def __init__(self):
        self.state = 0 # 0=unlocked; 1=unlocked but waiting task pending resume; 2=locked
        self.waiting = core.Queue() # Queue of Tasks waiting to acquire this Lock
    def locked(self):
        return self.state == 2
    def release(self):
        if self.state != 2:
            raise RuntimeError
        if self.waiting.peek():
            # Task(s) waiting on lock, schedule first Task
            core._queue.push_head(self.waiting.pop_head())
            self.state = 1
        else:
            # No Task waiting so unlock
            self.state = 0
    async def acquire(self):
        if self.state != 0 or self.waiting.peek():
            # Lock unavailable, put the calling Task on the waiting queue
            self.waiting.push_head(core.cur_task)
            # Set calling task's data to the lock's queue so it can be removed if needed
            core.cur_task.data = self.waiting
            try:
                yield
            except core.CancelledError as er:
                if self.state == 1:
                    # Cancelled while pending on resume, schedule next waiting Task
                    self.state = 2
                    self.release()
                raise er
        # Lock available, set it as locked
        self.state = 2
        return True
    async def __aenter__(self):
        return await self.acquire()
    async def __aexit__(self, exc_type, exc, tb):
        return self.release()
