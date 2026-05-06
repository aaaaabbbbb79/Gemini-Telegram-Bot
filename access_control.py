from asyncio import to_thread

from storage import (
    create_access_request,
    get_access_requests_enabled,
    get_access_status,
    list_approved_access,
    review_access_request,
    set_access_requests_enabled,
)

admin_user_ids: set[int] = set()


def init_admin_user_ids(user_ids: str) -> None:
    global admin_user_ids
    ids = {
        int(user_id.strip())
        for user_id in user_ids.split(",")
        if user_id.strip()
    }
    if not ids:
        raise ValueError("At least one admin user id must be configured")
    admin_user_ids = ids


def is_admin(user_id: int) -> bool:
    return user_id in admin_user_ids


def get_admin_user_ids() -> set[int]:
    return admin_user_ids


def get_access_subject(message) -> tuple[str, int]:
    return "user", message.from_user.id


async def get_subject_access_status(subject_type: str, subject_id: int) -> str | None:
    return await to_thread(get_access_status, subject_type, subject_id)


async def are_access_requests_enabled() -> bool:
    return await to_thread(get_access_requests_enabled)


async def set_access_request_enabled(enabled: bool) -> None:
    await to_thread(set_access_requests_enabled, enabled)


async def is_subject_authorized(subject_type: str, subject_id: int, actor_user_id: int) -> bool:
    if is_admin(actor_user_id):
        return True
    return await get_subject_access_status(subject_type, subject_id) == "approved"


async def is_user_authorized(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    return await get_subject_access_status("user", user_id) == "approved"


async def request_access(message) -> tuple[str, bool]:
    subject_type, subject_id = get_access_subject(message)
    if is_admin(message.from_user.id):
        return "approved", False

    return await to_thread(
        create_access_request,
        subject_type,
        subject_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name,
        None,
        message.from_user.id,
    )


async def review_access(subject_type: str, subject_id: int, status: str, reviewed_by: int) -> None:
    await to_thread(review_access_request, subject_type, subject_id, status, reviewed_by)


async def revoke_access(subject_type: str, subject_id: int, reviewed_by: int) -> None:
    await review_access(subject_type, subject_id, "revoked", reviewed_by)


async def get_approved_access_records() -> list[dict[str, object]]:
    return await to_thread(list_approved_access)
