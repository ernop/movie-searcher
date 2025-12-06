"""
Pydantic models for request/response validation in Movie Searcher API.
"""
from typing import Literal

from pydantic import BaseModel


class MovieInfo(BaseModel):
    path: str
    name: str
    length: float | None = None
    created: str | None = None
    size: int | None = None


class SearchRequest(BaseModel):
    query: str


class LaunchRequest(BaseModel):
    movie_id: int
    subtitle_path: str | None = None
    close_existing_vlc: bool = True
    start_time: float | None = None  # Start time in seconds


class ChangeStatusRequest(BaseModel):
    movie_id: int
    movieStatus: str | None = None  # None = unset, "watched", "unwatched", "want_to_watch"


class RatingRequest(BaseModel):
    movie_id: int
    rating: int  # 1-5 only


class ConfigRequest(BaseModel):
    movies_folder: str | None = None
    local_target_folder: str | None = None
    settings: dict | None = None


class FolderRequest(BaseModel):
    path: str


class CleanNameTestRequest(BaseModel):
    text: str


class ScreenshotsIntervalRequest(BaseModel):
    movie_id: int
    every_minutes: float = 3
    subtitle_path: str | None = None  # Path to subtitle file to burn in (if any)


class AiSearchRequest(BaseModel):
    query: str
    provider: Literal["openai", "anthropic"] = "anthropic"


class PlaylistCreateRequest(BaseModel):
    name: str


class PlaylistAddMovieRequest(BaseModel):
    movie_id: int


class MovieListUpdateRequest(BaseModel):
    title: str | None = None
    is_favorite: bool | None = None


class OpenUrlsRequest(BaseModel):
    urls: list[str]


class CheckMoviesRequest(BaseModel):
    movies: list[str]  # List of "Title Year" strings
