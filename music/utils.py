async def delete_message_safe(msg) -> bool:
    if msg:
        try:
            await msg.delete()
            return True
        except Exception:
            pass
    return False


def coerce_duration(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def create_progress_bar(elapsed: int, total: int, length: int = 14) -> str:
    if total <= 0:
        return "🔘" + "━" * (length - 1)
    progress = min(1.0, max(0.0, elapsed / float(total)))
    filled_length = int(round(length * progress))
    filled_length = min(length, max(0, filled_length))
    bar = "━" * filled_length + "🔘" + "━" * (length - filled_length)
    return bar
