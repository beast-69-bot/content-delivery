"""
middlewares/fsm_timeout.py
FSM Timeout middleware: Expire inactive FSM sessions after 5 minutes.
"""
import asyncio
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

active_timers: Dict[int, asyncio.Task] = {}

class FSMTimeoutMiddleware(BaseMiddleware):
    def __init__(self, timeout_seconds: int = 300):
        self.timeout_seconds = timeout_seconds

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        state: FSMContext = data.get("state")
        bot = data.get("bot")
        
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            
        if user_id and state:
            # Check if there is an active state
            current_state = await state.get_state()
            
            # Cancel existing timer
            if user_id in active_timers:
                active_timers[user_id].cancel()
                del active_timers[user_id]
                
            if current_state:
                # Start new timer
                active_timers[user_id] = asyncio.create_task(
                    self.expiry_timer(user_id, state, bot)
                )
                
        return await handler(event, data)
        
    async def expiry_timer(self, user_id: int, state: FSMContext, bot):
        try:
            await asyncio.sleep(self.timeout_seconds)
            current_state = await state.get_state()
            if current_state:
                await state.clear()
                if user_id in active_timers:
                    del active_timers[user_id]
                if bot:
                    try:
                        await bot.send_message(
                            user_id, 
                            "⏰ Session expired due to inactivity.\nPlease start again."
                        )
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
