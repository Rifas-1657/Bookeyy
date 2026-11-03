"""
app/api/notes.py — Notes CRUD (IDOR-safe).

GET    /me/notes
POST   /me/notes
PATCH  /me/notes/{id}
DELETE /me/notes/{id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_db
from app.api.page_refs import page_numbers
from app.db.models import Note, User

router = APIRouter(prefix="/me/notes", tags=["library"])


MAX_NOTE_LENGTH = 2000


class NoteIn(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    chapter_number: int = Field(ge=1)
    page_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)


class NoteUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_NOTE_LENGTH)


class NoteOut(BaseModel):
    id: str
    course_id: str
    chapter_number: int
    page_id: str
    page_number: int
    text: str
    created_at: str
    updated_at: str


def _note_out(note: Note, page_number: int) -> NoteOut:
    return NoteOut(
        id=note.id, course_id=note.course_id, chapter_number=note.chapter_number,
        page_id=note.page_id, page_number=page_number, text=note.text,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
    )


@router.get("", response_model=list[NoteOut])
async def list_notes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[NoteOut]:
    result = await db.execute(
        select(Note)
        .where(Note.user_id == current_user.id)
        .order_by(Note.updated_at.desc())
    )
    rows = result.scalars().all()
    numbers = await page_numbers(db, {r.page_id for r in rows})
    return [_note_out(r, numbers[r.page_id]) for r in rows]


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NoteOut:
    note = Note(
        user_id=current_user.id,
        course_id=body.course_id,
        chapter_number=body.chapter_number,
        page_id=body.page_id,
        text=body.text,
    )
    db.add(note)
    await db.commit()
    numbers = await page_numbers(db, {note.page_id})
    return _note_out(note, numbers[note.page_id])


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: str,
    body: NoteUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NoteOut:
    result = await db.execute(
        select(Note).where(
            Note.id == note_id,
            Note.user_id == current_user.id,  # IDOR
        )
    )
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    note.text = body.text
    await db.commit()
    numbers = await page_numbers(db, {note.page_id})
    return _note_out(note, numbers[note.page_id])


@router.delete("/{note_id}", status_code=204, response_class=Response)
async def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    result = await db.execute(
        select(Note).where(
            Note.id == note_id,
            Note.user_id == current_user.id,  # IDOR
        )
    )
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    await db.delete(note)
    await db.commit()
    return Response(status_code=204)
