# MicroPython uasyncio module
# MIT license; Copyright (c) 2019-2020 Damien P. George

from . import core

# Event class for primitive events that can be waited on, set, and cleared
class Event:
    def __init__(self):
        self.state = 0 # 0=unset; 1=set
        self.waiting = core.Queue() # Queue of Tasks waiting on completion of this event
    def set(self):
        # Event becomes set, schedule any tasks waiting on it
        while self.waiting.peek():
            core._queue.push_head(self.waiting.pop_head())
        self.state = 1
    def clear(self):
        self.state = 0
    async def wait(self):
        if self.state == 0:
            # Event not set, put the calling task on the event's waiting queue
            self.waiting.push_head(core.cur_task)
            # Set calling task's data to the event's queue so it can be removed if needed
            core.cur_task.data = self.waiting
            yield
        return True
