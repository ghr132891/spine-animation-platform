"""Texture-only Spine editing. Original skeletons/atlases are immutable bytes.

Independent implementation based on the public Spine atlas specification.
No code from spine-animation-ai is used by this application.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image, ImageEnhance

MAX_BYTES = 150 * 1024 * 1024
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class UserError(ValueError):
    pass


def safe_name(name):
    name = str(name).replace("\\", "/")
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(x in ("..", "") for x in p.parts) or ":" in name or "\x00" in name:
        raise UserError("文件路径不合法")
    return str(p)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def png_bytes(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def read_image(data):
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            raise UserError("图片过大，单页最多 4000 万像素")
        im.load()
        return im.convert("RGBA")
    except UserError:
        raise
    except Exception as e:
        raise UserError("无法读取图片，请使用有效的 PNG 图片") from e


def straight(image, pma):
    if not pma:
        return image.copy()
    a = np.array(image).astype(np.float32)
    alpha = a[..., 3:4]
    a[..., :3] = np.where(alpha > 0, np.minimum(255, a[..., :3] * 255 / np.maximum(alpha, 1)), 0)
    return Image.fromarray(np.rint(a).astype(np.uint8))


def packed(image, pma, original_alpha):
    a = np.array(image)
    a[..., 3] = original_alpha
    if pma:
        a[..., :3] = np.rint(a[..., :3].astype(np.float32) * original_alpha[..., None] / 255).astype(np.uint8)
    return Image.fromarray(a)


def parse_atlas(text):
    """Supports legacy xy/size/orig/offset and compact bounds/offsets fields."""
    pages, regions = [], []
    page = region = None
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line:
            page = region = None
            continue
        if ":" not in line:
            if page is None:
                page = {"name": safe_name(line), "fields": {}}
                pages.append(page)
                region = None
            else:
                region = {"id": f"r{len(regions)}", "name": line, "page": len(pages)-1, "fields": {}}
                regions.append(region)
        else:
            if page is None:
                raise UserError("atlas 缺少贴图页名称")
            k, value = line.split(":", 1)
            (region if region is not None else page)["fields"][k.strip()] = value.strip()
    if not pages or not regions:
        raise UserError("atlas 中未找到贴图页或部件")
    def ints(fields, key, count, default):
        if key not in fields:
            return default
        try:
            result = [int(x.strip()) for x in fields[key].split(",")]
        except ValueError as e:
            raise UserError(f"atlas {key} 必须是整数") from e
        if len(result) != count:
            raise UserError(f"atlas {key} 字段长度不正确")
        return result
    for p in pages:
        p["size"] = ints(p["fields"], "size", 2, [0, 0])
        p["pma"] = p["fields"].get("pma", "false").lower() == "true"
    for r in regions:
        f = r["fields"]
        r["x"], r["y"], r["width"], r["height"] = ints(f, "bounds", 4, ints(f, "xy", 2, [0, 0])+ints(f, "size", 2, [0, 0]))
        r["offset_x"], r["offset_y"], r["original_width"], r["original_height"] = ints(f, "offsets", 4, ints(f, "offset", 2, [0, 0])+ints(f, "orig", 2, [r["width"], r["height"]]))
        rotation = f.get("rotate", "false").lower()
        try:
            r["rotation"] = 90 if rotation == "true" else 0 if rotation == "false" else int(rotation) % 360
        except ValueError as e:
            raise UserError(f"无法识别 {r['name']} 的旋转角度") from e
        if r["rotation"] not in (0, 90, 180, 270):
            raise UserError("此版本支持 0/90/180/270 度图集旋转")
        r["packed_width"], r["packed_height"] = (r["height"], r["width"]) if r["rotation"] in (90, 270) else (r["width"], r["height"])
        r["index"] = ints(f, "index", 1, [-1])[0]
        if min(r[k] for k in ("width", "height", "original_width", "original_height")) <= 0:
            raise UserError(f"部件 {r['name']} 的尺寸无效")
        if r["original_width"] * r["original_height"] > MAX_PIXELS:
            raise UserError("部件原始画布过大")
        if min(r["x"], r["y"], r["offset_x"], r["offset_y"]) < 0 or r["offset_x"]+r["width"] > r["original_width"] or r["offset_y"]+r["height"] > r["original_height"]:
            raise UserError(f"部件 {r['name']} 的裁切偏移无效")
    return pages, regions


def decode_files(items):
    files = {}
    total = 0
    for item in items:
        name = safe_name(item["name"])
        try:
            data = base64.b64decode(item["data"], validate=True)
        except Exception as e:
            raise UserError("上传文件编码无效") from e
        total += len(data)
        if total > MAX_BYTES:
            raise UserError("导入资源总计不能超过 150 MB")
        if name.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    if len(z.infolist()) > 3000:
                        raise UserError("ZIP 文件数量过多")
                    for info in z.infolist():
                        if info.is_dir() or "__MACOSX" in info.filename or PurePosixPath(info.filename).name.startswith("."):
                            continue
                        entry = safe_name(info.filename)
                        if info.file_size > MAX_BYTES or sum(map(len, files.values())) + info.file_size > MAX_BYTES:
                            raise UserError("ZIP 解压后超过 150 MB")
                        if entry in files:
                            raise UserError(f"重名文件：{entry}")
                        files[entry] = z.read(info)
            except (zipfile.BadZipFile, RuntimeError) as e:
                raise UserError("ZIP 无效或已加密") from e
        else:
            if name in files:
                raise UserError(f"重名文件：{name}")
            files[name] = data
    if not files:
        raise UserError("请选择资源文件")
    # Remove an optional shared ZIP/folder wrapper while preserving subfolders.
    while all("/" in n for n in files) and len({n.split('/')[0] for n in files}) == 1:
        files = {n.split('/', 1)[1]: data for n, data in files.items()}
    return files


class Project:
    def __init__(self, directory, files=None, label=None):
        self.directory = Path(directory)
        self.id = self.directory.name
        self.lock = threading.RLock()
        self.jobs = {}
        if files is not None:
            self.files = files
            self.label = label or "Spine 角色"
            self.revision = 0
            self.changed = []
            self.undo_info = None
            self._analyze()
            for name, content in files.items():
                p = self.directory / "original" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)
            self.pages_data = {p["file"]: files[p["file"]] for p in self.pages}
            self.save()
        else:
            meta = json.loads((self.directory / "project.json").read_text())
            self.files = {n: (self.directory / "original" / n).read_bytes() for n in meta["files"]}
            self.label = meta["label"]
            self.revision = meta["revision"]
            self.changed = meta["changed"]
            self.undo_info = meta.get("undo")
            self._analyze()
            self.pages_data = {p["file"]: (self.directory / "working" / p["file"]).read_bytes() for p in self.pages}
        self._init_history(files is not None)

    def _init_history(self, is_new):
        index=self.directory/'history/index.json'
        if index.exists():
            self.history_entries=json.loads(index.read_text())
            return
        self.history_entries=[]
        original_pages={p['file']:self.files[p['file']] for p in self.pages}
        self._record_history('导入原始资源', 'import', original_pages, 0, [])
        if not is_new and self.undo_info:
            undo_pages={n:(self.directory/'undo'/n).read_bytes() for n in self.pages_data}
            if self.undo_info.get('revision',0)>0:
                self._record_history('升级前保留的上一步','recovered',undo_pages,self.undo_info['revision'],self.undo_info.get('changed',[]))
        if not is_new and self.revision>0:
            self._record_history('升级前已保存的状态','recovered',self.pages_data,self.revision,self.changed)

    def _record_history(self, label, kind, pages, revision, changed):
        blobs={}
        for name,data in pages.items():
            digest=sha(data);path=self.directory/'history/blobs'/(digest+'.png')
            if not path.exists():
                path.parent.mkdir(parents=True,exist_ok=True)
                temp=path.with_suffix('.tmp');temp.write_bytes(data);temp.replace(path)
            blobs[name]=digest
        entry={'id':uuid.uuid4().hex,'label':str(label)[:100],'kind':kind,'revision':revision,'created_at':time.time(),'changed':list(changed),'pages':blobs}
        entries=[*self.history_entries,entry]
        atomic_json(self.directory/'history/index.json',entries)
        self.history_entries=entries
        return entry

    def save_version(self, label, revision):
        with self.lock:
            self.check_revision(revision)
            return self._record_history(str(label).strip() or '手动保存版本','manual',self.pages_data,self.revision,self.changed)

    def history(self):
        return [{k:v for k,v in e.items() if k!='pages'} for e in reversed(self.history_entries)]

    def history_pages(self, snapshot_id):
        entry=next((e for e in self.history_entries if e['id']==snapshot_id),None)
        if entry is None:raise UserError('历史版本不存在')
        pages={}
        for name,digest in entry['pages'].items():
            data=(self.directory/'history/blobs'/(digest+'.png')).read_bytes()
            if sha(data)!=digest:raise UserError('历史贴图校验失败，已阻止恢复')
            pages[name]=data
        if set(pages)!=set(self.pages_data):raise UserError('历史图集与当前项目不匹配')
        return entry,pages

    def restore_version(self, snapshot_id, revision):
        with self.lock:
            self.check_revision(revision)
            entry,pages=self.history_pages(snapshot_id)
            # Check every page before touching current state.
            for name,data in pages.items():
                old,new=read_image(self.files[name]),read_image(data)
                if old.size!=new.size or not np.array_equal(np.array(old)[...,3],np.array(new)[...,3]):
                    raise UserError('历史贴图尺寸或透明通道异常，已阻止恢复')
            self.checkpoint()
            self.pages_data=pages
            self.commit('恢复：'+entry['label'])

    def _analyze(self):
        atlases = [n for n in self.files if n.lower().endswith(".atlas")]
        if not atlases:
            atlases = [n for n in self.files if n.lower().endswith(".txt") and b"size:" in self.files[n]]
        if len(atlases) != 1:
            raise UserError("请一次导入一套资源，需要且只能有一个 .atlas 文件（可包含多页贴图）")
        self.atlas = atlases[0]
        try:
            self.pages, self.regions = parse_atlas(self.files[self.atlas].decode("utf-8-sig"))
        except UnicodeDecodeError as e:
            raise UserError("atlas 必须是 UTF-8 文本") from e
        skeletons = []
        for n, data in self.files.items():
            if n.lower().endswith(".json"):
                try:
                    js = json.loads(data)
                    if isinstance(js, dict) and isinstance(js.get("bones"), list):
                        skeletons.append((n, js))
                except (ValueError, UnicodeDecodeError):
                    continue
        if len(skeletons) > 1:
            raise UserError("检测到多个骨骼 JSON，请一次只导入一套角色资源")
        self.skeleton_data = skeletons[0][1] if skeletons else None
        if skeletons:
            self.skeleton = skeletons[0][0]
            self.version = str(self.skeleton_data.get("skeleton", {}).get("spine", "未知"))
            self.animations = list(self.skeleton_data.get("animations", {}))
            skins = self.skeleton_data.get("skins", [])
            self.skins = list(skins) if isinstance(skins, dict) else [s["name"] for s in skins]
        else:
            binary = [n for n in self.files if n.endswith(".skel")]
            if len(binary) != 1:
                raise UserError("缺少骨骼文件，请上传一个 .json 或 .skel 文件")
            self.skeleton = binary[0]
            match = re.search(rb"(?:3\.[5-8]|4\.[0-3])\.[0-9]{1,4}", self.files[self.skeleton][:160])
            self.version = match[0].decode() if match else "未知"
            self.animations, self.skins = [], []
        self.runtime = next((v for v in ("3.8", "4.2") if self.version == v or self.version.startswith(v+".")), None)
        self.by_region = {r["id"]: r for r in self.regions}
        for p in self.pages:
            p["file"] = safe_name(str(PurePosixPath(self.atlas).parent / p["name"]))
            if p["file"] not in self.files:
                raise UserError(f"缺少图集引用的贴图：{p['file']}，请保留相对目录")
            if not p["file"].lower().endswith(".png"):
                raise UserError("当前版本仅支持 PNG 贴图页")
            image = read_image(self.files[p["file"]])
            if any(p["size"]) and list(image.size) != p["size"]:
                raise UserError(f"贴图 {p['file']} 的尺寸与 atlas 不一致")
            p["size"] = list(image.size)
        if len({p['file'] for p in self.pages}) != len(self.pages):
            raise UserError("同一 atlas 不能重复引用同名贴图页")
        for r in self.regions:
            w, h = self.pages[r["page"]]["size"]
            if r["x"]+r["packed_width"] > w or r["y"]+r["packed_height"] > h:
                raise UserError(f"部件 {r['name']} 超出图集边界")

    def save(self):
        for name, data in self.pages_data.items():
            p = self.directory / "working" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            temp = p.with_suffix(".tmp")
            temp.write_bytes(data)
            temp.replace(p)
        meta = {"files": list(self.files), "label": self.label, "revision": self.revision, "changed": self.changed, "undo": self.undo_info}
        p = self.directory / "project.json"
        temp = p.with_suffix(".tmp")
        temp.write_text(json.dumps(meta, ensure_ascii=False))
        temp.replace(p)

    def summary(self):
        jobs=[]
        for f in (self.directory/'jobs').glob('*.json'):
            job=json.loads(f.read_text())
            jobs.append({k:job[k] for k in ('id','region','model','revision','status','candidate_ready','created_at','error','prompt','mode','poll_error') if k in job})
        jobs.sort(key=lambda j:j.get('created_at',0),reverse=True)
        return {"id": self.id, "name": self.label, "revision": self.revision, "version": self.version, "runtime": self.runtime,
                "skeleton": self.skeleton, "atlas": self.atlas, "animations": self.animations, "skins": self.skins,
                "pages": self.pages, "regions": self.regions, "changed": self.changed, "can_undo": bool(self.undo_info),
                "bones": len(self.skeleton_data["bones"]) if self.skeleton_data else None,"jobs":jobs,"history_count":len(self.history_entries)}

    def page_image(self, index, original=False):
        p = self.pages[int(index)]
        image = read_image(self.files[p["file"]] if original else self.pages_data[p["file"]])
        return straight(image, p["pma"])

    def part(self, region_id, original=False):
        r = self.by_region[region_id]
        image = self.page_image(r["page"], original)
        cut = image.crop((r["x"], r["y"], r["x"]+r["packed_width"], r["y"]+r["packed_height"]))
        cut = cut.rotate(-r["rotation"], expand=True)
        full = Image.new("RGBA", (r["original_width"], r["original_height"]))
        full.paste(cut, (r["offset_x"], r["original_height"]-r["offset_y"]-r["height"]))
        return full

    def check_revision(self, revision):
        if revision != self.revision:
            raise UserError("资源已在其他操作中更新，请刷新后再试")

    def checkpoint(self):
        for name, data in self.pages_data.items():
            p = self.directory / "undo" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.undo_info = {"changed": self.changed[:], "revision": self.revision}

    def commit(self, label='更新贴图'):
        self.revision += 1
        # Derive changed regions from actual pixels, including atlas aliases.
        self.changed = []
        for index, page in enumerate(self.pages):
            old = np.array(read_image(self.files[page["file"]]))
            new = np.array(read_image(self.pages_data[page["file"]]))
            for r in self.regions:
                if r["page"] == index:
                    box = (slice(r["y"], r["y"]+r["packed_height"]), slice(r["x"], r["x"]+r["packed_width"]))
                    if not np.array_equal(old[box], new[box]):
                        self.changed.append(r["id"])
        self.save()
        self._record_history(label,'automatic',self.pages_data,self.revision,self.changed)

    def _put_part(self, region_id, candidate):
        r = self.by_region[region_id]
        expected = (r["original_width"], r["original_height"])
        if candidate.size != expected:
            raise UserError(f"新图必须是 {expected[0]}×{expected[1]}，请先按原部件画布对齐；不会静默缩放")
        ox, top = r["offset_x"], r["original_height"]-r["offset_y"]-r["height"]
        patch = candidate.crop((ox, top, ox+r["width"], top+r["height"]))
        patch = patch.rotate(r["rotation"], expand=True)
        page = self.pages[r["page"]]
        raw = read_image(self.pages_data[page["file"]])
        source = read_image(self.files[page["file"]])
        box = (r["x"], r["y"], r["x"]+r["packed_width"], r["y"]+r["packed_height"])
        alpha = np.array(source.crop(box))[..., 3]
        patch = packed(patch, page["pma"], alpha)
        # Preserve RGB outside the original silhouette, including atlas bleed.
        arr, prior = np.array(patch), np.array(source.crop(box))
        arr[alpha == 0] = prior[alpha == 0]
        raw.paste(Image.fromarray(arr), (r["x"], r["y"]))
        self.pages_data[page["file"]] = png_bytes(raw)

    def replace(self, region_id, data, revision, label=None):
        candidate = read_image(data)
        r = self.by_region[region_id]
        if candidate.size != (r["original_width"], r["original_height"]):
            raise UserError(f"新图必须是 {r['original_width']}×{r['original_height']}，请下载原部件对齐后替换")
        with self.lock:
            self.check_revision(revision)
            self.checkpoint()
            self._put_part(region_id, candidate)
            self.commit(label or '替换部件：'+r['name'])

    def replace_page(self, index, data, revision):
        candidate = read_image(data)
        p = self.pages[int(index)]
        if list(candidate.size) != p["size"]:
            raise UserError(f"整页图集必须是 {p['size'][0]}×{p['size'][1]}")
        with self.lock:
            self.check_revision(revision)
            self.checkpoint()
            source = read_image(self.files[p["file"]])
            alpha = np.array(source)[..., 3]
            result = np.array(packed(candidate, p["pma"], alpha))
            result[alpha == 0] = np.array(source)[alpha == 0]
            self.pages_data[p["file"]] = png_bytes(Image.fromarray(result))
            self.commit('替换图集：'+p['name'])

    def recolor(self, ids, hue, saturation, brightness, revision):
        if not ids or any(r not in self.by_region for r in ids):
            raise UserError("请至少选择一个有效部件")
        if not all(math.isfinite(float(x)) for x in (hue, saturation, brightness)) or not -180 <= hue <= 180 or not 0 <= saturation <= 2 or not .2 <= brightness <= 2:
            raise UserError("调色参数超出范围")
        with self.lock:
            self.check_revision(revision)
            # Absolute adjustment from the original prevents repeated slider accumulation.
            edits = []
            for region_id in ids:
                part = self.part(region_id, original=True)
                hsv = np.array(part.convert("RGB").convert("HSV"))
                hsv[..., 0] = (hsv[..., 0].astype(np.int16) + round(hue * 255 / 360)) % 256
                im = Image.fromarray(hsv, "HSV").convert("RGB")
                im = ImageEnhance.Color(im).enhance(saturation)
                im = ImageEnhance.Brightness(im).enhance(brightness).convert("RGBA")
                edits.append((region_id, im))
            self.checkpoint()
            for region_id, im in edits:
                self._put_part(region_id, im)
            self.commit(f'调色：{len(ids)} 个部件')

    def reset(self, ids, revision):
        with self.lock:
            self.check_revision(revision)
            self.checkpoint()
            if ids is None:
                self.pages_data = {p["file"]: self.files[p["file"]] for p in self.pages}
            else:
                for region_id in ids:
                    r = self.by_region[region_id]
                    page = self.pages[r["page"]]
                    box = (r["x"], r["y"], r["x"]+r["packed_width"], r["y"]+r["packed_height"])
                    image = read_image(self.pages_data[page["file"]])
                    image.paste(read_image(self.files[page["file"]]).crop(box), (r["x"], r["y"]))
                    self.pages_data[page["file"]] = png_bytes(image)
            self.commit('恢复原图')

    def undo(self, revision):
        with self.lock:
            self.check_revision(revision)
            if not self.undo_info:
                raise UserError("没有可撤销的修改")
            self.pages_data = {p["file"]: (self.directory / "undo" / p["file"]).read_bytes() for p in self.pages}
            self.undo_info = None
            self.commit('撤销上一步')

    def validate(self):
        checks = []
        texture_names = set(self.pages_data)
        for name, data in self.files.items():
            if name in texture_names:
                old, new = read_image(data), read_image(self.pages_data[name])
                checks.append({"file": name, "kind": "texture", "dimensions_equal": old.size == new.size,
                               "alpha_equal": old.size == new.size and np.array_equal(np.array(old)[..., 3], np.array(new)[..., 3]),
                               "before_sha256": sha(data), "after_sha256": sha(self.pages_data[name])})
            else:
                actual = (self.directory / "original" / name).read_bytes()
                checks.append({"file": name, "kind": "immutable", "byte_identical": actual == data, "sha256": sha(data)})
        ok = all(c.get("byte_identical", c.get("dimensions_equal", True) and c.get("alpha_equal", True)) for c in checks)
        return {"passed": ok, "revision": self.revision, "skeleton": self.skeleton, "version": self.version,
                "skeleton_unchanged": (self.directory/'original'/self.skeleton).read_bytes()==self.files[self.skeleton],
                "atlas_unchanged": (self.directory/'original'/self.atlas).read_bytes()==self.files[self.atlas], "changed_regions": self.changed,
                "checks": checks, "scope": "文件不变量校验；仍需播放所有动作检查换皮后的视觉效果"}

    def export(self):
        with self.lock:
            report = self.validate()
            if not report["passed"]:
                raise UserError("不变量校验未通过，已阻止导出")
            out = io.BytesIO()
            report_name='_reskin-validation.json'
            index=2
            while report_name in self.files:
                report_name=f'_reskin-validation-{index}.json';index+=1
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in self.files.items():
                    z.writestr(name, self.pages_data.get(name, data))
                z.writestr(report_name, json.dumps(report, indent=2, ensure_ascii=False))
            return out.getvalue()

    def raw_data(self, original=False):
        raw = {}
        for name in [self.skeleton, self.atlas, *self.pages_data]:
            data = self.files[name] if original or name not in self.pages_data else self.pages_data[name]
            mime = "image/png" if name.endswith('.png') else "application/json" if name.endswith('.json') else "application/octet-stream"
            raw[name] = f"data:{mime};base64,"+base64.b64encode(data).decode()
        return {"skeleton": self.skeleton, "atlas": self.atlas, "rawDataURIs": raw, "pma": any(p["pma"] for p in self.pages), "runtime": self.runtime}
