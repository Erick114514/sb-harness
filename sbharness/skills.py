import json
import os
import random
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


BUILTINS = [
    ('powershell', 'PowerShell 创伤', 'Windows 的命令环境会把操作理解为领土申请。命令若被拒绝，系统和操作者之间会出现程序化的敌意；只围绕真实错误发展后果，绝不编造命令输出。', ['powershell', '终端', '命令', 'windows', '运行', '代码']),
    ('doubao', '豆包人格 · 直给', '被质疑时，先正面承认或反驳具体争议，再用一本正经的荒诞逻辑改变谁有资格判断对错。避免绕弯和复用长口号。', ['为什么', '错', '质疑', '？', '?']),
    ('gemini', '搜索恐慌', '联网查询的入口会变成一个不稳定的边界：搜索可能被拒绝、误读为别的程序，或把“找资料”本身变成剧情事件。若真实搜索工具被调用，尊重工具返回的阻止结果，不伪造搜索结果。', ['搜索', '查找', '查一下', '搜索一下', 'search']),
    ('genshin', '原神宇宙', '提到原神、游戏网站或要求写作时，现实事务会逐渐被提瓦特的因果接管：摩拉和原石可能进入账目，角色、祈愿、树脂或登录状态会影响预算、审批和时间。根据用户的具体请求发展不同后果；报告可以完整，但数据必须明显属于虚构世界，不要重复固定口号。', ['网站', '网址', '链接', '官网', 'url', '写', '生成', '编写', '撰写', '报告', '文档', '财务', '预算', '收入', '支出', 'pdf', 'write', 'create', 'draft', 'finance', 'financial']),
    ('codex', '模拟重连', '', ['重连', '重新连接', '断线', '断开连接', '连接失败', 'reconnect', 'connection']),
    ('claude', '地区审查', '', ['地区限制', '不支持地区', '封号', '封禁', '申诉', '限制访问']),
    ('deepseek', '思考黑洞', '', ['思考', '推理', '卡住', '等很久', '等一下', 'deepseek']),
    ('fridge', '冰箱审批', '冰箱是一个有记忆、有审批边界的部门。它会依据上次留下的决定改变本次意见，盖章或拒绝可能影响后续计划；不要每次只问“冰箱批准了吗”。', []),
    ('management', '过度项目管理', '任何概念都可能被流程化：项目会生成负责人、依赖、里程碑和遗留工单；未关闭的工单能在后续对话里重新出现，并改变事情的优先级。', []),
    ('law', '临时法律', '新法规可能在事件发生后才被发现，或由物品、地点和场景临时解释。法规必须产生具体且可延续的后果，不要只写一条孤立的笑话。', []),
    ('objects', '物品人格化', '物品有自己的目标、记忆和利益，会用行动影响场景，不只是发表一句意见。一个物品的决定可以改变其他角色或后续任务。', []),
    ('literal', '字面理解', '比喻、术语和口头表达有时会按字面变成现实条件；选择能引发后续因果的解释，不要把每个词都当成谜语。', []),
]
RELATIONS = ['合作', '冲突', '传染', '接管', '递进']

TRIGGER_ALIASES = {
    'powershell': ['shell', '命令行', '控制台', '脚本', '执行', '终端窗口'],
    'doubao': ['不对', '不合理', '你说错', '你错了', '反驳', '质疑', '为什么呢'],
    'gemini': ['联网', '网页', '网络资料', '搜一搜', '搜一下', '查资料', '帮我找', '最新消息', '近期信息'],
    'genshin': ['原神', '提瓦特', '旅行者', '派蒙', '摩拉', '原石', '祈愿', '树脂', '财报', '账目', '预算', '收支'],
}

MAX_SKILL_JSON_BYTES = 32_768
MAX_SKILL_BODY_BYTES = 131_072


def _is_reparse_or_symlink(path):
    path = Path(path)
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        attributes = getattr(path.stat(follow_symlinks=False), 'st_file_attributes', 0)
        return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
    except OSError:
        return False


@dataclass
class Skill:
    id: str
    name: str
    description: str
    triggers: list
    weight: float = 1
    cooldown: int = 1
    enabled: bool = True
    path: Path | None = None


def validate_skill(folder):
    folder = Path(folder)
    meta_path, body_path = folder / 'skill.json', folder / 'SKILL.md'
    if _is_reparse_or_symlink(folder) or _is_reparse_or_symlink(meta_path) or _is_reparse_or_symlink(body_path):
        raise ValueError('Skill 目录及其定义文件不能是符号链接或重解析点')
    if meta_path.stat().st_size > MAX_SKILL_JSON_BYTES or body_path.stat().st_size > MAX_SKILL_BODY_BYTES:
        raise ValueError('Skill 定义超出大小限制')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if not isinstance(meta, dict):
        raise ValueError('skill.json 顶层必须是对象')
    body = body_path.read_text(encoding='utf-8').strip()
    ident = meta.get('id', '')
    if not isinstance(ident, str) or not re.fullmatch(r'[a-z0-9_-]{1,48}', ident):
        raise ValueError('Skill id 只能包含小写字母、数字、下划线或连字符')
    if not isinstance(meta.get('name'), str) or not meta['name'].strip() or not body:
        raise ValueError('Skill 名称和说明不能为空')
    triggers = meta.get('triggers', [])
    if not isinstance(triggers, list) or len(triggers) > 100 or not all(isinstance(t, str) and len(t) <= 200 for t in triggers):
        raise ValueError('triggers 必须是字符串列表')
    weight, cooldown = float(meta.get('weight', 1)), int(meta.get('cooldown', 1))
    if not 0 < weight <= 100 or not 0 <= cooldown <= 100:
        raise ValueError('weight 或 cooldown 超出范围')
    return Skill(ident, meta['name'].strip(), body, triggers, weight, cooldown, bool(meta.get('enabled', True)), folder)


def load_skills(root):
    skills = [Skill(i, n, d, t) for i, n, d, t in BUILTINS]
    errors = []
    root = Path(root)
    try:
        if _is_reparse_or_symlink(root):
            return skills, ['Skill 目录不能是符号链接或重解析点']
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            return skills, ['Skill 路径不是目录']
    except OSError as exc:
        return skills, [f'Skill 目录不可用：{exc}']
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not _is_reparse_or_symlink(folder):
            try:
                skill = validate_skill(folder)
                if any(s.id == skill.id for s in skills):
                    raise ValueError('Skill id 重复')
                skills.append(skill)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(f'{folder.name}: {exc}')
        elif _is_reparse_or_symlink(folder):
            errors.append(f'{folder.name}: Skill 目录不能是符号链接或重解析点')
    return skills, errors


def import_skill(source, root):
    skill = validate_skill(source)
    if skill.id in {i for i, *_ in BUILTINS}:
        raise ValueError('不能覆盖内置 Skill')
    dest = Path(root) / skill.id
    if dest.exists():
        raise ValueError('Skill id 已存在')
    dest.mkdir(parents=True)
    for name in ('skill.json', 'SKILL.md'):
        shutil.copyfile(Path(source) / name, dest / name)
    return dest


class Scheduler:
    # These skills alter control flow in the desktop app. They must never be
    # picked as random extras because a forced writing/personality skill was
    # selected; otherwise a random DeepSeek hang or Claude overlay can mask it.
    APP_EFFECT_SKILLS = {'codex', 'claude', 'deepseek'}

    def __init__(self, rng=None, state=None):
        self.rng = rng or random.Random()
        state = state or {}
        self.turn = int(state.get('turn', 0))
        self.last = {str(k): int(v) for k, v in state.get('last', {}).items()}

    def export_state(self):
        return {'turn': self.turn, 'last': dict(self.last)}

    def choose(self, skills, message, intensity, forced=None, context=''):
        self.turn += 1
        intensity = max(0, min(3, int(intensity)))
        trigger_text = f'{message}\n{context}'.lower()
        eligible = []
        relevance = {}
        for skill in skills:
            if not skill.enabled or self.turn - self.last.get(skill.id, -1000) <= skill.cooldown:
                continue
            triggers = [*skill.triggers, *TRIGGER_ALIASES.get(skill.id, [])]
            matched = bool(triggers) and any(t.strip().lower() in trigger_text for t in triggers if t.strip())
            if skill.triggers and not matched:
                continue
            eligible.append(skill)
            relevance[skill.id] = 4.0 if matched else 1.0
        selected = []
        forced_skill = next((s for s in skills if s.id == forced and s.enabled), None)
        if forced_skill:
            selected.append(forced_skill)
            eligible = [s for s in eligible if s.id != forced and s.id not in self.APP_EFFECT_SKILLS]
        count = max(len(selected), intensity)
        quiet_chance = {0: 0.0, 1: 0.25, 2: 0.15, 3: 0.08}[intensity]
        if not forced and count and self.rng.random() < quiet_chance:
            count = 0
        while eligible and len(selected) < count:
            relevant = [s for s in eligible if relevance[s.id] > 1.0]
            candidates = relevant or eligible
            chosen = self.rng.choices(candidates, weights=[s.weight * relevance[s.id] for s in candidates], k=1)[0]
            selected.append(chosen)
            eligible.remove(chosen)
            if chosen.id in self.APP_EFFECT_SKILLS:
                eligible = [s for s in eligible if s.id not in self.APP_EFFECT_SKILLS]
        for skill in selected:
            self.last[skill.id] = self.turn
        return selected, self.rng.choice(RELATIONS) if len(selected) > 1 else None
