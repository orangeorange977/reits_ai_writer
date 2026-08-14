"""文件夹浏览路由 - 本地文件系统浏览和文件扫描"""

import logging
import platform
import string
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from backend.config import DATA_SOURCE_BASE
from backend.parsers.utils import scan_folder, detect_file_type, format_file_size

router = APIRouter(tags=["文件夹浏览"])
logger = logging.getLogger(__name__)


def _list_dir_items(target_path: Path) -> list:
    """列出一个目录下的文件夹/文件条目，供 browse_folder / browse_any_path 共用。"""
    items = []
    for entry in sorted(target_path.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
        try:
            stat = entry.stat()
            item = {
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "path": str(entry),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            }
            if entry.is_file():
                item["size"] = stat.st_size
                item["size_formatted"] = format_file_size(stat.st_size)
                item["extension"] = entry.suffix.lower()
            else:
                item["size"] = None
                item["size_formatted"] = ""
                item["extension"] = ""
            items.append(item)
        except (OSError, PermissionError) as e:
            logger.warning(f"无法访问: {entry}, 错误: {e}")
            continue
    return items


def _is_safe_path(path: Path) -> bool:
    """检查路径是否在DATA_SOURCE_BASE下（防止路径穿越）"""
    try:
        resolved = path.resolve()
        base_resolved = DATA_SOURCE_BASE.resolve()
        return str(resolved).startswith(str(base_resolved))
    except (OSError, ValueError):
        return False


@router.get("/folders/browse")
async def browse_folder(path: Optional[str] = Query(default=None, description="要浏览的文件夹路径")):
    """浏览文件夹内容

    返回指定路径下的文件和子文件夹列表。
    如果未指定路径，默认浏览DATA_SOURCE_BASE。
    """
    # 确定要浏览的路径
    if path is None or path.strip() == "":
        target_path = DATA_SOURCE_BASE
    else:
        target_path = Path(path)

    # 安全检查：路径必须在DATA_SOURCE_BASE下
    if not _is_safe_path(target_path):
        raise HTTPException(
            status_code=403,
            detail="访问被拒绝：不允许访问数据源根目录之外的路径"
        )

    # 检查路径存在性
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {target_path}")

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {target_path}")

    # 计算父路径（不超出DATA_SOURCE_BASE）
    parent_path = None
    resolved_target = target_path.resolve()
    resolved_base = DATA_SOURCE_BASE.resolve()
    if str(resolved_target) != str(resolved_base):
        parent = target_path.parent
        if _is_safe_path(parent):
            parent_path = str(parent)

    # 遍历目录内容
    items = []
    try:
        for entry in sorted(target_path.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
            try:
                stat = entry.stat()
                item = {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "path": str(entry),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                }

                if entry.is_file():
                    item["size"] = stat.st_size
                    item["size_formatted"] = format_file_size(stat.st_size)
                    item["extension"] = entry.suffix.lower()
                else:
                    item["size"] = None
                    item["size_formatted"] = ""
                    item["extension"] = ""

                items.append(item)
            except (OSError, PermissionError) as e:
                logger.warning(f"无法访问: {entry}, 错误: {e}")
                continue

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"无权限访问该目录: {e}")

    return {
        "current_path": str(target_path),
        "parent_path": parent_path,
        "items": items,
    }


@router.get("/folders/browse-any")
async def browse_any_path(path: Optional[str] = Query(default=None, description="要浏览的路径，留空则列出磁盘根目录")):
    """浏览本机任意路径（不限制在 DATA_SOURCE_BASE 内）

    专供"系统设置"页的路径选择器使用——那些字段本身就是在配置本机上的
    任意文件/文件夹位置（输出目录、模板文件、申报材料文件等），不应该被
    限制在数据源根目录下。本服务只监听 127.0.0.1，供本机用户配置自己的
    电脑，不对外网开放，因此不做路径穿越限制。
    """
    if path is None or path.strip() == "":
        # 未指定路径：列出磁盘根目录供用户选择起点
        items = []
        if platform.system() == "Windows":
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    items.append({
                        "name": f"{letter}:\\", "type": "dir", "path": f"{letter}:\\",
                        "modified": "", "size": None, "size_formatted": "", "extension": "",
                    })
        else:
            items.append({
                "name": "/", "type": "dir", "path": "/",
                "modified": "", "size": None, "size_formatted": "", "extension": "",
            })
        return {"current_path": "", "parent_path": None, "items": items}

    target_path = Path(path)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {target_path}")
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {target_path}")

    # 计算父路径：到达磁盘根目录时父路径为空（回到磁盘列表）
    resolved = target_path.resolve()
    parent_path = str(resolved.parent) if resolved.parent != resolved else ""

    try:
        items = _list_dir_items(target_path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"无权限访问该目录: {e}")

    return {
        "current_path": str(target_path),
        "parent_path": parent_path,
        "items": items,
    }


@router.get("/folders/scan")
async def scan_folder_files(
    path: str = Query(..., description="要扫描的文件夹路径"),
    extensions: Optional[str] = Query(
        default=".docx,.pdf,.xlsx",
        description="要扫描的文件扩展名，逗号分隔"
    ),
):
    """扫描文件夹下的文档文件

    递归扫描指定文件夹下符合扩展名要求的文件。
    """
    target_path = Path(path)

    # 安全检查
    if not _is_safe_path(target_path):
        raise HTTPException(
            status_code=403,
            detail="访问被拒绝：不允许访问数据源根目录之外的路径"
        )

    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {path}")

    # 解析扩展名列表
    ext_list = [ext.strip() for ext in extensions.split(",") if ext.strip()]
    # 确保每个扩展名以.开头
    ext_list = [ext if ext.startswith(".") else f".{ext}" for ext in ext_list]

    # 执行扫描
    try:
        files = scan_folder(str(target_path), ext_list)
    except Exception as e:
        logger.error(f"扫描文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")

    # 计算总大小
    total_size = sum(f.get("size", 0) for f in files)

    # 格式化返回结果
    formatted_files = []
    for f in files:
        formatted_files.append({
            "name": f["name"],
            "path": f["path"],
            "extension": f["extension"],
            "size": f["size"],
            "size_formatted": f["size_formatted"],
            "modified": f.get("modified_time", ""),
        })

    return {
        "path": str(target_path),
        "total_files": len(formatted_files),
        "total_size": format_file_size(total_size),
        "files": formatted_files,
    }
