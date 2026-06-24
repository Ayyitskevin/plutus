"""Run blocking sync work off the asyncio event loop."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def run_sync[T](func: Callable[..., T], /, *args, **kwargs) -> T:
    return await asyncio.to_thread(func, *args, **kwargs)