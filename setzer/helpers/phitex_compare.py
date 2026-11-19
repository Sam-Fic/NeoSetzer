#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''`phitex session compare --json` 的解析与 AI 编辑基线的管理。

compare 的输出是单个 JSON 对象（phitex 1.0.3+，crates/cli/src/compare.rs 的
changes_for）：changes[].{n, kind, page, bbox, file, line, old_text, new_text,
section}。**page/bbox 是 diff PDF（JOB-diff 旁路构建的 latexdiff 标记文档）
上的坐标**，不是正式 PDF 的——所以矩形只画在 diff 视图里；file/line 指当前
版本文件（c.new），正式 PDF 上的位置反查在 AiDiff 侧做。

bbox 是 [x0, y0, x1, y1]，points、页左上原点、y 向下；而 NeoSetzer 预览的
synctex 矩形惯例是 v = 底边距页顶的距离（y 轴向上）。两套坐标的"页顶"是同
一个参照物，差别只在命名方向：底边在 y 向下系里就是 y1，故 v = y1。

基线有两条通道，由 write_git_snapshot / write_baseline 决定：
* **文件通道**：旧主文件副本的路径。PhiTeX 只把主文件文本换成副本
  （compare.rs 的 OldCopy），\\input/\\include 子文件读当前版——多文件文档
  的子文件改动看不出来。
* **git 通道**（compare 接受任意 rev）：发一份"私有快照"提交——papilot 的
  做法：临时 index + write-tree + commit-tree，用户的 HEAD/staging 一概不碰，
  提交不可达、只以 sha 存在。多文件全量 diff，子文件改动可见（已实测）。

本模块刻意不 import gi：解析、坐标换算、基线读写都是纯逻辑，CI 无显示直跑。
'''

import difflib
import json
import os

import subprocess

# AI 编辑前的正文快照目录。放在文档目录的 .neosetzer/ 下（与项目构建配置
# build.json 同一惯例）：随文档走、应用重启后仍在——agent 跑几分钟期间
# NeoSetzer 若重启，基线还在，下一次构建仍能出 diff。
_BASELINE_SUBDIR = os.path.join('.neosetzer', 'ai-baseline')


def parse_compare_json(stdout):
    '''compare --json 的 stdout → payload dict；解析不出返回 None。

    phitex 把 JSON 打在 stdout（错误路径也是：{"error": ..., "status": N}），
    前后可能混有非 JSON 行，故逐行找第一个以 { 开头且能整体 loads 的行——
    与 PhitexSession._first_json_object 同一语义，这里独立实现是为了让本
    模块不依赖 build_system 的导入链。
    '''
    for line in (stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def compare_failed(payload):
    '''payload 是否是请求级失败（REV 不存在、无 watch 之类）。

    compare 的退出码语义：0 成功、1 diff 构建有 TeX 错误（payload 仍带
    changes，只是 page/bbox 可能全 null）、3/4 是请求被拒，那种情况 JSON
    形如 {"error": ..., "status": N}。TeX 错误不算失败——错误信息交给
    调用方决定要不要提示，changes 该展示还是展示。非 dict（None / 解析
    垃圾）一律按失败处理，调用方无需再单独判空。
    '''
    if not isinstance(payload, dict):
        return True
    return 'error' in payload and 'changes' not in payload


def changes_to_rectangles(changes):
    '''compare 的 changes → 预览矩形列表（synctex 矩形惯例 + kind）。

    输出元素：{page, h, v, width, height, kind, n}。page/bbox 为 null 的
    change 是"这次构建里没显示出来"（compare.rs 对未显示的 change 给
    null），跳过；kind/n 原样带上，presenter 按 kind 着色、弹窗按 n 反查。
    '''
    rectangles = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        bbox = change.get('bbox')
        page = change.get('page')
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            # 空盒（理论不该出现）对画高亮毫无意义，还可能在合并时传染邻盒。
            continue
        rectangles.append({'page': page,
                           'h': x0,
                           'v': y1,
                           'width': x1 - x0,
                           'height': y1 - y0,
                           'kind': change.get('kind') or 'change',
                           'n': change.get('n')})
    return rectangles


# 移动配对的判据（papilot 的规则）：一处删除与一处新增的"正文指纹"完全一致，
# 或字母数字骨架足够相似（归一化后 ≥ MOVE_MIN_CHARS 才参与相似配对——短片段
# 比如 "the" 到处都是，配出来必是误报）。kind 为 moved 的 change 原样带
# old_text/new_text（弹窗照常显示前后文），presenter 画蓝色。
MOVE_MIN_CHARS = 20
MOVE_SIMILARITY = 0.85


def _move_fingerprint(text):
    '''移动配对用的正文指纹：只留字母数字、小写、压空白——排版性的差异
    （断词、连字符、标点挪动、宏名变化）不该阻止"同一段话被挪走了"的判定。'''
    characters = []
    for character in (text or ''):
        if character.isalnum():
            characters.append(character.lower())
    return ''.join(characters)


def pair_moves(changes):
    '''把"删了 A 处、另一处冒出同样的 A"配成 moved（就地修改 kinds）。

    PhiTeX 的 diff 引擎没有移动检测：被挪动的段落会变成一对 del+add，用户
    看到的是一红一绿两块，而语义上它是同一段话换了地方（papilot 用 ≥20 个
    字母数字的共享来提名移动）。这里在 compare 结果上做后处理配对：

    1. 归一化指纹完全相等的 del/add 优先配对（每处最多配一次）；
    2. 指纹 ≥ MOVE_MIN_CHARS 的剩余项按相似度（difflib ratio ≥
       MOVE_SIMILARITY）贪心配对，相似度高的先配——容忍排版本里的微调。
    配上的双方 kind 改为 'moved'，互记 move_of=n。就地修改，返回原列表。
    '''
    deletions = []
    additions = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        kind = change.get('kind')
        if kind == 'del':
            deletions.append(change)
        elif kind == 'add':
            additions.append(change)
    if not deletions or not additions:
        return changes

    fingerprints = {}
    for change in deletions + additions:
        fingerprints[id(change)] = _move_fingerprint(
            change.get('old_text') if change.get('kind') == 'del' else change.get('new_text'))

    # 第一轮：指纹完全相等。
    pending_deletions = []
    addition_by_fingerprint = {}
    for change in additions:
        addition_by_fingerprint.setdefault(fingerprints[id(change)], []).append(change)
    for change in deletions:
        matches = addition_by_fingerprint.get(fingerprints[id(change)])
        if matches:
            other = matches.pop(0)
            _mark_moved(change, other)
        else:
            pending_deletions.append(change)

    # 第二轮：长片段按相似度贪心配对（相似度高的先配，配过就消耗掉）。
    pairs = []
    remaining_additions = [change for change in additions
                           if change.get('kind') == 'add']
    for deletion in pending_deletions:
        deletion_fingerprint = fingerprints[id(deletion)]
        if len(deletion_fingerprint) < MOVE_MIN_CHARS:
            continue
        for addition in remaining_additions:
            addition_fingerprint = fingerprints[id(addition)]
            if len(addition_fingerprint) < MOVE_MIN_CHARS:
                continue
            ratio = difflib.SequenceMatcher(None, deletion_fingerprint,
                                            addition_fingerprint).ratio()
            if ratio >= MOVE_SIMILARITY:
                pairs.append((ratio, deletion, addition))
    pairs.sort(key=lambda pair: (-pair[0], pair[1].get('n') or 0, pair[2].get('n') or 0))
    used_deletions = set()
    used_additions = set()
    for _, deletion, addition in pairs:
        if id(deletion) in used_deletions or id(addition) in used_additions:
            continue
        used_deletions.add(id(deletion))
        used_additions.add(id(addition))
        _mark_moved(deletion, addition)
    return changes


def _mark_moved(deletion, addition):
    deletion['kind'] = 'moved'
    addition['kind'] = 'moved'
    deletion['move_of'] = addition.get('n')
    addition['move_of'] = deletion.get('n')


def baseline_path_for(doc_dir, basename):
    '''AI 基线快照的落盘路径：{doc_dir}/.neosetzer/ai-baseline/{basename}。

    basename 带原扩展名（main.tex → main.tex）：compare 只认得 LaTeX 文本，
    保留扩展名也让用户在文件管理器里认得出这份快照是什么。
    '''
    return os.path.join(doc_dir, _BASELINE_SUBDIR, basename)


def write_baseline(text, doc_dir, basename):
    '''写入快照，返回路径；写不了（目录只读等）返回 None。

    覆盖式写：基线语义就是"最近一次 AI 发送时的正文"，旧的没保留价值。
    '''
    path = baseline_path_for(doc_dir, basename)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    except OSError:
        return None
    return path


def read_baseline(path):
    '''读回快照文本；文件没了/读不了返回 None（照常清掉过期状态）。'''
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return None


def baseline_matches(text, baseline_text):
    '''当前正文与基线是否一致（逐字节比较，百 KB 级文档也就毫秒级）。

    baseline_text 是武装时记下的快照正文（文件通道写入磁盘的那份，git 通道
    就是发送时刻的 buffer 文本）——不再回头读文件，两条通道一个比较逻辑。'''
    return text == baseline_text


def clear_baseline(doc_dir, basename):
    '''删掉快照文件（容错：不存在/删不掉都无所谓）。'''
    try:
        os.remove(baseline_path_for(doc_dir, basename))
    except OSError:
        pass


def write_git_snapshot(directory):
    '''把 directory 的文本文件现状存成一份"私有快照"提交，返回 sha。

    papilot 的做法：临时 GIT_INDEX_FILE + read-tree + add -A + write-tree +
    commit-tree。用户的 HEAD、staging、worktree 一概不动；提交对象不可达
    （没有引用指它），只以 sha 存在——compare 传这个 sha 即可 diff 出发份。
    实测 PhiTeX（gitoxide）按 sha 读对象，不要求可达。

    * 临时 index 放**系统临时目录**（按目录哈希命名），绝不放仓库里——
      否则用户的 git status 会多出一个 untracked 文件。
    * 从 HEAD 起步（保留与用户历史的父子关系）；无 HEAD（空仓库）从空树。
    * add -A 尊重 .gitignore：构建产物一般被忽略，不会进快照；未跟踪的
      新 .tex 会被收进来（那正是基线的一部分）。
    * commit-tree 显式给身份，不依赖用户配置了 user.name。
    * 目录不是 git 仓库/任何一步失败 → 返回 None（调用方回退文件通道）。
    '''
    import hashlib
    import tempfile
    key = hashlib.sha1(os.path.abspath(directory or '.').encode('utf-8')).hexdigest()[:16]
    index = os.path.join(tempfile.gettempdir(),
                         'neosetzer-snapshot-index-{}.idx'.format(key))
    env = dict(os.environ)
    env['GIT_INDEX_FILE'] = index
    base = ['git', '-C', directory,
            '-c', 'user.name=neosetzer-snapshot', '-c', 'user.email=neosetzer@localhost']

    def run(*arguments):
        try:
            return subprocess.run(base + list(arguments), env=env,
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None

    read_tree = run('read-tree', 'HEAD')
    if read_tree is None or read_tree.returncode != 0:
        # 无 HEAD（刚 git init）：从空树起步，快照即"全部文件都是新的"。
        read_tree = run('read-tree', '--empty')
        if read_tree is None or read_tree.returncode != 0:
            return None
    add = run('add', '-A', '--', '.')
    if add is None or add.returncode != 0:
        return None
    tree = run('write-tree')
    if tree is None or tree.returncode != 0:
        return None
    commit = run('commit-tree', tree.stdout.strip(),
                 '-m', 'NeoSetzer AI baseline snapshot')
    if commit is None or commit.returncode != 0:
        return None
    sha = commit.stdout.strip()
    return sha or None


def looks_like_rev(reference):
    '''compare 的 REV 参数是 git rev 还是旧文件路径。

    PhiTeX 自己按"名字是否指向存在的文件"判别，但我们的调用约定更简单：
    文件通道**永远传绝对路径**（write_baseline 的返回值），git rev（sha、
    HEAD~N、分支名）永远不是绝对路径——所以非绝对路径即 rev。判给 rev 的
    字符串原样传给 PhiTeX，**不能** abspath：40 位 sha 接上 cwd 就成了不
    存在的新路径。'''
    if not reference:
        return False
    return not os.path.isabs(reference)
