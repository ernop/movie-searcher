"""
Pydantic models for request/response validation in Movie Searcher API.
"""
from pydantic import BaseModel
from typing import Optional, Literal


class MovieInfo(BaseModel):
    path: str
    name: str
    length: Optional[float] = None
    created: Optional[str] = None
    size: Optional[int] = None


class SearchRequest(BaseModel):
    query: str


class LaunchRequest(BaseModel):
    movie_id: int
    subtitle_path: Optional[str] = None
    close_existing_vlc: bool = True
    start_time: Optional[float] = None  # Start time in seconds


class ChangeStatusRequest(BaseModel):
    movie_id: int
    movieStatus: Optional[str] = None  # None = unset, "watched", "unwatched", "want_to_watch"


class RatingRequest(BaseModel):
    movie_id: int
    rating: int  # 1-5 only


class ConfigRequest(BaseModel):
    movies_folder: Optional[str] = None
    local_target_folder: Optional[str] = None
    settings: Optional[dict] = None


class FolderRequest(BaseModel):
    path: str


class CleanNameTestRequest(BaseModel):
    text: str


class ScreenshotsIntervalRequest(BaseModel):
    movie_id: int
    every_minutes: float = 3
    subtitle_path: Optional[str] = None  # Path to subtitle file to burn in (if any)


class AiSearchRequest(BaseModel):
    query: str
    provider: Literal["openai", "anthropic"] = "anthropic"


class PlaylistCreateRequest(BaseModel):
    name: str


class PlaylistAddMovieRequest(BaseModel):
    movie_id: int


class MovieListUpdateRequest(BaseModel):
    title: Optional[str] = None
    is_favorite: Optional[bool] = None
