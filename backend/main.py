from google import genai
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from datetime import date, datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from pwdlib import PasswordHash

from database import get_db
from models import User, Task as TaskModel


# --------------------------------------------------
# Environment configuration
# --------------------------------------------------

project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

session_secret = os.getenv("SESSION_SECRET_KEY")
if not session_secret:
    raise RuntimeError("SESSION_SECRET_KEY is missing from the .env file.")


# --------------------------------------------------
# FastAPI application
# --------------------------------------------------

app = FastAPI(title="FocusFlow API")

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="focusflow_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# Password hashing
# --------------------------------------------------

password_hasher = PasswordHash.recommended()


# --------------------------------------------------
# Request models
# --------------------------------------------------

class UserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    due_date: date | None = None
    priority: Literal["Low", "Medium", "High"] = "Medium"


class TaskCompletion(BaseModel):
    completed: bool


class FocusPlanRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    time_available: int | None = Field(default=None, ge=5, le=1440)


# --------------------------------------------------
# Health check
# GET /api/health
# --------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "healthy", "application": "FocusFlow"}


# --------------------------------------------------
# Shared authentication helper
# --------------------------------------------------

def get_authenticated_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please sign in.",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User session is invalid.",
        )
    return user


# --------------------------------------------------
# User registration
# POST /api/v1/auth/register
# --------------------------------------------------

@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register_user(user_data: UserCreate, db: Session = Depends(get_db)):
    full_name = user_data.full_name.strip()
    email = str(user_data.email).lower()
    if not full_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Full name cannot be empty.",
        )

    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    new_user = User(
        full_name=full_name,
        email=email,
        password_hash=password_hasher.hash(user_data.password),
    )
    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return {
        "id": new_user.id,
        "full_name": new_user.full_name,
        "email": new_user.email,
        "created_at": new_user.created_at,
    }


# --------------------------------------------------
# User login
# POST /api/v1/auth/login
# --------------------------------------------------

@app.post("/api/v1/auth/login")
def login_user(
    login_data: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    email = str(login_data.email).lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not password_hasher.verify(
        login_data.password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    request.session["user_id"] = user.id
    return {
        "message": "Login successful",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
        },
    }


# --------------------------------------------------
# Get currently logged-in user
# GET /api/v1/auth/me
# --------------------------------------------------

@app.get("/api/v1/auth/me")
def get_current_user(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# --------------------------------------------------
# User logout
# POST /api/v1/auth/logout
# --------------------------------------------------

@app.post("/api/v1/auth/logout")
def logout_user(request: Request):
    request.session.clear()
    return {"message": "Logged out successfully."}


# --------------------------------------------------
# Convert a task into a JSON-friendly response
# --------------------------------------------------

def task_to_dict(task: TaskModel) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "priority": task.priority,
        "completed": task.completed,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "user_id": task.user_id,
    }


# --------------------------------------------------
# Create a task
# POST /api/v1/tasks
# --------------------------------------------------

@app.post("/api/v1/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    task_data: TaskCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    title = task_data.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Task title cannot be empty.",
        )

    new_task = TaskModel(
        title=title,
        description=task_data.description,
        due_date=task_data.due_date,
        priority=task_data.priority,
        completed=False,
        completed_at=None,
        user_id=user.id,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return task_to_dict(new_task)


# --------------------------------------------------
# Retrieve tasks belonging to the logged-in user
# GET /api/v1/tasks
# --------------------------------------------------

@app.get("/api/v1/tasks")
def get_tasks(request: Request, db: Session = Depends(get_db)):
    user = get_authenticated_user(request, db)
    tasks = (
        db.query(TaskModel)
        .filter(TaskModel.user_id == user.id)
        .order_by(TaskModel.created_at.desc())
        .all()
    )
    return {"tasks": [task_to_dict(task) for task in tasks]}


# --------------------------------------------------
# Mark a task completed or reopen it
# PATCH /api/v1/tasks/{task_id}/completion
# --------------------------------------------------

@app.patch("/api/v1/tasks/{task_id}/completion")
def update_task_completion(
    task_id: int,
    completion_data: TaskCompletion,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    task = (
        db.query(TaskModel)
        .filter(TaskModel.id == task_id, TaskModel.user_id == user.id)
        .first()
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found.",
        )

    task.completed = completion_data.completed
    task.completed_at = datetime.now(timezone.utc) if task.completed else None
    db.commit()
    db.refresh(task)
    return {
        "message": "Task completed successfully." if task.completed else "Task reopened successfully.",
        "task": task_to_dict(task),
    }


# --------------------------------------------------
# AI helpers
# --------------------------------------------------

def get_gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI service is not configured.")
    return genai.Client(api_key=api_key)


def normalize_task_title(value: str) -> str:
    """Normalize a title for conservative duplicate detection."""
    value = value.casefold().strip()
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def find_task_duplicate(title: str, existing_tasks: list[TaskModel]) -> tuple[str, TaskModel] | None:
    candidate = normalize_task_title(title)
    if not candidate:
        return None

    # Exact normalized matches are blocked in the UI, so users cannot accidentally
    # add the same task twice with different capitalization or punctuation.
    for task in existing_tasks:
        if candidate == normalize_task_title(task.title):
            return ("exact_duplicate", task)

    # Similar-but-not-identical titles are flagged for the user to review.
    for task in existing_tasks:
        existing = normalize_task_title(task.title)
        if min(len(candidate), len(existing)) >= 10:
            ratio = SequenceMatcher(None, candidate, existing).ratio()
            if ratio >= 0.86:
                return ("possible_duplicate", task)
    return None


def parse_ai_json(text: str) -> dict:
    """Parse JSON from Gemini, allowing a fenced JSON response as fallback."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Gemini did not return valid JSON.")
        result = json.loads(cleaned[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("Gemini returned an unexpected response format.")
    return result


# --------------------------------------------------
# AI focus planner (read-only)
# POST /api/v1/ai/focus-plan
# --------------------------------------------------

@app.post("/api/v1/ai/focus-plan")
def create_focus_plan(
    payload: FocusPlanRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    client = get_gemini_client()

    active_tasks = (
        db.query(TaskModel)
        .filter(TaskModel.user_id == user.id, TaskModel.completed.is_(False))
        .order_by(TaskModel.due_date.asc())
        .limit(50)
        .all()
    )
    task_details = [
        {
            "title": task.title,
            "description": task.description or "No description",
            "priority": task.priority,
            "due_date": str(task.due_date) if task.due_date else "No due date",
        }
        for task in active_tasks
    ]
    available_time = f"{payload.time_available} minutes" if payload.time_available is not None else "Not specified"
    prompt = f"""
You are the AI Focus Planner inside FocusFlow, a productivity app.
Create a practical, realistic plan for the user's work session.
USER'S GOAL: {payload.goal}
TIME AVAILABLE: {available_time}
USER'S INCOMPLETE TASKS: {task_details}
Instructions:
- Prioritize urgent deadlines and High-priority tasks where appropriate.
- Respect the user's available time and break work into manageable steps.
- If the goal does not match listed tasks, say so and still help with the goal.
- If there are no incomplete tasks, suggest useful next steps without inventing existing tasks.
- Clearly distinguish suggestions from existing tasks.
- Do not claim to create, edit, complete, or reprioritize any task.
- Be concise and return readable plain text with headings and a numbered plan.
"""
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
        )
        plan = (response.text or "").strip()
        if not plan:
            raise HTTPException(status_code=502, detail="The AI returned an empty plan. Please try again.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="The AI planner is temporarily unavailable. Please try again.")

    # This endpoint only reads tasks and returns text; it never writes to the database.
    return {"plan": plan, "tasks_considered": len(active_tasks)}


# --------------------------------------------------
# AI task suggestions with duplicate checks (read-only)
# POST /api/v1/ai/task-suggestions
# --------------------------------------------------

@app.post("/api/v1/ai/task-suggestions")
def create_ai_task_suggestions(
    payload: FocusPlanRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_authenticated_user(request, db)
    client = get_gemini_client()

    # Only this user's tasks are included in the prompt or duplicate checks.
    existing_tasks = (
        db.query(TaskModel)
        .filter(TaskModel.user_id == user.id)
        .order_by(TaskModel.created_at.desc())
        .all()
    )
    # Keep the prompt compact while duplicate detection still checks every task.
    existing_task_details = [
        {"title": task.title, "description": task.description or "", "completed": bool(task.completed)}
        for task in existing_tasks[:200]
    ]
    available_time = f"{payload.time_available} minutes" if payload.time_available is not None else "Not specified"

    prompt = f"""
You create task suggestions for FocusFlow. You MUST NOT save or modify tasks.
User's goal/discussion: {payload.goal}
Time available: {available_time}
User's existing tasks (some may be completed): {json.dumps(existing_task_details, ensure_ascii=False)}

Return ONLY a valid JSON object with this exact shape:
{{"suggestions":[{{"title":"short actionable task title","description":"clear concise description","priority":"Low|Medium|High","estimated_minutes":30}}]}}
Rules:
- Suggest only concrete tasks that directly support the user's goal.
- Suggest 0 to 5 tasks; do not create filler tasks.
- Use priority exactly Low, Medium, or High.
- estimated_minutes must be an integer from 5 to 1440.
- Do not repeat existing tasks, including close paraphrases or title variations.
- Do not claim any task has already been saved.
- If the user's goal is vague or there are no genuinely useful tasks to suggest, return an empty suggestions array.
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        parsed = parse_ai_json(response.text or "")
        raw_suggestions = parsed.get("suggestions", [])
        if not isinstance(raw_suggestions, list):
            raise ValueError("The suggestions field must be a list.")
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="The AI task suggestions are temporarily unavailable. Please try again.",
        )

    suggestions = []
    seen_titles: set[str] = set()
    for item in raw_suggestions[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        if not title or len(title) > 200:
            continue
        if len(description) > 5000:
            description = description[:5000]
        normalized = normalize_task_title(title)
        if not normalized or normalized in seen_titles:
            continue
        seen_titles.add(normalized)

        priority_value = item.get("priority", "Medium")
        priority = priority_value if priority_value in ("Low", "Medium", "High") else "Medium"
        try:
            estimated_minutes = int(item.get("estimated_minutes", 30))
        except (TypeError, ValueError):
            estimated_minutes = 30
        estimated_minutes = max(5, min(1440, estimated_minutes))

        duplicate_match = find_task_duplicate(title, existing_tasks)
        if duplicate_match:
            duplicate_status, duplicate = duplicate_match
            duplicate_of_id = duplicate.id
            duplicate_of_title = duplicate.title
        else:
            duplicate_status = "new"
            duplicate_of_id = None
            duplicate_of_title = None

        suggestions.append({
            "title": title,
            "description": description,
            "priority": priority,
            "estimated_minutes": estimated_minutes,
            "duplicate_status": duplicate_status,
            "duplicate_of_id": duplicate_of_id,
            "duplicate_of_title": duplicate_of_title,
        })

    # Suggestions are not saved here. Only POST /api/v1/tasks saves after the user clicks Add.
    return {"suggestions": suggestions, "existing_tasks_checked": len(existing_tasks)}
