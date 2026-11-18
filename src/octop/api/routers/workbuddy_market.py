"""WorkBuddy market import (fork): browse, preview and install.

Catalog data comes from :mod:`octop.infra.workbuddy`. Installs reuse the
agent-workspace and skill-package install targets of the ``skills`` and
``skill_packages`` routers so permission checks and post-install hooks stay
identical to SkillHub installs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_any_permission, require_permission
from octop.api.routers import skill_packages as skill_packages_router
from octop.api.routers import skills as skills_router
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.skills.install import SkillAlreadyExistsError, commit_skill_install
from octop.infra.skills.skill_packages import SkillPackageError, SkillPackageTooLarge
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale
from octop.infra.workbuddy import skills as wb_skills
from octop.infra.workbuddy.archive import WorkBuddyFetchError

router = APIRouter(prefix="/workbuddy")


class LocalizedText(BaseModel):
    zh: str | None = None
    en: str | None = None


class WorkBuddySkillCard(BaseModel):
    slug: str = Field(..., description="Skill directory name; becomes the installed skill id.")
    name: LocalizedText
    summary: LocalizedText
    description: str = Field("", description="Full description used for skill routing.")
    category: LocalizedText
    tags: dict[str, list[str]]
    examples: dict[str, list[str]]
    featured: bool
    version: str
    icon_url: str | None = Field(None, description="Icon endpoint (needs auth), if any.")
    mentions_workbuddy: bool = Field(
        ..., description="SKILL.md mentions WorkBuddy/CodeBuddy; may rely on its tools."
    )
    file_count: int
    total_bytes: int


class WorkBuddySkillList(BaseModel):
    items: list[WorkBuddySkillCard]
    categories: list[LocalizedText]
    version: str = Field(..., description="Cached market version (content hash prefix).")


class WorkBuddySkillFile(BaseModel):
    path: str
    size: int


class WorkBuddySkillDetail(WorkBuddySkillCard):
    skill_md: str = Field(..., description="SKILL.md text (truncated at 256 KB).")
    files: list[WorkBuddySkillFile]


class WorkBuddyInstallBody(BaseModel):
    slug: str
    overwrite: bool = True
    enable: bool = True


class WorkBuddyInstallResult(BaseModel):
    installed: bool
    name: str


def _card(skill: wb_skills.WorkBuddySkill) -> dict[str, Any]:
    return {
        "slug": skill.slug,
        "name": skill.name,
        "summary": skill.summary,
        "description": skill.description,
        "category": skill.category,
        "tags": skill.tags,
        "examples": skill.examples,
        "featured": skill.featured,
        "version": skill.version,
        "icon_url": wb_skills.icon_api_url(skill.slug) if skill.has_icon else None,
        "mentions_workbuddy": skill.mentions_workbuddy,
        "file_count": skill.file_count,
        "total_bytes": skill.total_bytes,
    }


def _market_error(exc: Exception) -> OctopError:
    return OctopError(
        ErrorCode.SKILL_IMPORT_FAILED,
        f"WorkBuddy market unavailable: {exc}",
        details={"reason": str(exc)},
        status=502,
    )


def _not_found(slug: str) -> OctopError:
    return OctopError(ErrorCode.NOT_FOUND, f"WorkBuddy skill not found: {slug}")


async def _resolve(slug: str, request: Request) -> Any:
    locale = resolve_request_locale(request)
    try:
        return await asyncio.to_thread(wb_skills.resolve_install_package, slug)
    except wb_skills.WorkBuddySkillNotFound as exc:
        raise _not_found(slug) from exc
    except WorkBuddyFetchError as exc:
        raise _market_error(exc) from exc
    except SkillPackageTooLarge as exc:
        raise OctopError(ErrorCode.SKILL_IMPORT_FAILED, str(exc), status=413) from exc
    except SkillPackageError as exc:
        raise OctopError.localized(ErrorCode.SKILL_IMPORT_FAILED, locale, reason=str(exc)) from exc


@router.get(
    "/skills",
    response_model=WorkBuddySkillList,
    summary="List WorkBuddy market skills",
    description="Reads the cached WorkBuddy skill market (refreshed every 6 hours; "
    "`refresh=true` re-checks now).",
)
async def list_workbuddy_skills(
    q: str = "",
    category: str = "",
    refresh: bool = False,
    _user: User = Depends(require_any_permission("skill_market", "skill_packages")),
) -> dict[str, Any]:
    try:
        items, categories, version = await asyncio.to_thread(
            wb_skills.list_skills, q, category, force=refresh
        )
    except (WorkBuddyFetchError, SkillPackageError) as exc:
        raise _market_error(exc) from exc
    return {"items": [_card(s) for s in items], "categories": categories, "version": version}


@router.get(
    "/skills/{slug}",
    response_model=WorkBuddySkillDetail,
    summary="Preview a WorkBuddy market skill",
)
async def get_workbuddy_skill(
    slug: str,
    _user: User = Depends(require_any_permission("skill_market", "skill_packages")),
) -> dict[str, Any]:
    try:
        skill = await asyncio.to_thread(wb_skills.get_skill, slug)
        preview = await asyncio.to_thread(wb_skills.skill_preview, slug)
    except wb_skills.WorkBuddySkillNotFound as exc:
        raise _not_found(slug) from exc
    except (WorkBuddyFetchError, SkillPackageError) as exc:
        raise _market_error(exc) from exc
    return {**_card(skill), **preview}


@router.get("/skills/{slug}/icon", summary="WorkBuddy market skill icon")
async def get_workbuddy_skill_icon(
    slug: str,
    _user: User = Depends(require_any_permission("skill_market", "skill_packages")),
) -> Response:
    try:
        data, media_type = await asyncio.to_thread(wb_skills.skill_icon, slug)
    except wb_skills.WorkBuddySkillNotFound as exc:
        raise _not_found(slug) from exc
    except (WorkBuddyFetchError, SkillPackageError) as exc:
        raise _market_error(exc) from exc
    headers = {"Cache-Control": "private, max-age=86400"}
    if media_type == "image/svg+xml":
        # SVG from a third party: never let it run script when opened directly.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return Response(content=data, media_type=media_type, headers=headers)


@router.post(
    "/skills/install/agents/{agent_id}",
    status_code=201,
    response_model=WorkBuddyInstallResult,
    summary="Install a WorkBuddy skill into an agent",
)
async def install_workbuddy_skill_to_agent(
    agent_id: str,
    body: WorkBuddyInstallBody,
    request: Request,
    as_user: int | None = None,
    user: Any = Depends(require_permission("skills")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    package = await _resolve(body.slug, request)
    ctx = await skills_router._ctx(agent_id, user=user, as_user=as_user, server=server)
    target = skills_router._AgentWorkspaceInstallTarget(
        workspace=ctx.workspace, config=ctx.config, server=server, agent_id=agent_id
    )
    await skills_router._guard_package_only_skill_write(
        ctx.workspace, ctx.config, server, package.slug, user
    )
    try:
        await commit_skill_install(target, package, overwrite=body.overwrite, enable=body.enable)
    except SkillAlreadyExistsError as exc:
        raise OctopError.localized(
            ErrorCode.SKILL_ALREADY_EXISTS, resolve_request_locale(request), name=exc.slug
        ) from exc
    return {"installed": True, "name": package.slug}


@router.post(
    "/skills/install/packages/{package_id}",
    status_code=201,
    response_model=WorkBuddyInstallResult,
    summary="Install a WorkBuddy skill into a global skill package",
)
async def install_workbuddy_skill_to_package(
    package_id: str,
    body: WorkBuddyInstallBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("skill_packages")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    store = skill_packages_router._store(server)
    row = skill_packages_router._package_or_404(store, package_id, locale=locale)
    store.assert_can_mutate(row, user)
    package = await _resolve(body.slug, request)
    target = skill_packages_router._PackageInstallTarget(
        store=store, package_id=package_id, server=server
    )
    try:
        await commit_skill_install(target, package, overwrite=body.overwrite)
    except SkillAlreadyExistsError as exc:
        raise OctopError.localized(ErrorCode.SKILL_ALREADY_EXISTS, locale, name=exc.slug) from exc
    return {"installed": True, "name": package.slug}
