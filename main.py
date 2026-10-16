import os
import json
import random
import datetime
import base64
import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import StarTools
import astrbot.api.message_components as Comp
from astrbot.core.star.filter.permission import PermissionType
import copy
import time


@register(
    "astrbot_plugin_gacha",
    "VW1024",
    "基于 pool 文件夹自动扫描的抽卡插件",
    "1.0.0",
    "https://github.com/vw1024/astrbot_plugin_gacha"
)
class GachaPlugin(Star):
    """
    多卡池抽卡插件

    ==================== 目录规范 ====================
    目录结构：
      pool/卡池名/pool.json             卡池配置
      pool/卡池名/weights.json          卡片权重
      pool/卡池名/default_announce.txt  卡池默认抽卡提示词
      pool/卡池名/cards/卡名/
        ├── *.png                      图片
        ├── desc.txt                   描述
        ├── announce.txt               抽卡提示词
        ├── name.txt                   展示名称
        ├── before.txt                 图片前气泡
        └── after.txt                  文本后气泡

    所有 txt 均为可选，空文件或缺失则不输出对应部分。

    ==================== 字段说明 ====================
    config.json：
      default_daily_limit   全局默认每日抽卡次数（正整数，默认 1）

    pool.json：
      display_name          卡池显示名（可选，默认用文件夹名）
      daily_limit           该卡池每日抽卡上限（正整数，默认 1）
      group_whitelist       群白名单列表（空 = 对所有群开放）

    weights.json：
      {卡名: 正整数权重}      缺失/非法自动降级为 50
                            概率 = 权重 / 所有卡权重之和

    records.json：
      键格式：{group_id}_{user_id}_{pool_name}_{date}  私聊 group_id=0
      值格式：{"count": 次数, "cards": [卡片名列表]}

    ==================== 用户指令 ====================
      /抽卡 卡池名                 抽一次卡
      /查看卡池                    列出所有卡池
      /查看次数 [卡池名]            查看今日抽卡次数
      /抽卡记录                   查看自己今日抽卡记录
      /抽卡帮助                   查看用户指令

    ==================== 管理员指令 ====================
      /新建卡池 卡池名              创建空卡池
      /设置显示名 卡池名 新名        修改卡池展示名称
      /设置次数                    修改卡池每日可抽卡次数
      /添加卡片 卡池名 卡名 [描述]    向卡池添加卡片
      /编辑卡片 卡池名 卡名 announce|name|desc|before|after [内容]
                                 替换修改或者删除对应文本
      /删除卡片 卡池名 卡名          删除卡片
      /删除卡池 卡池名              删除整个卡池
      /设置权重 卡池名 卡名 权重      修改单张卡片权重
      /查看权重 卡池名              查看卡池权重分布
      /添加白名单 卡池名 群号         添加群到白名单",
      /移除白名单 卡池名 群号         从白名单移除",
      /查看白名单 卡池名             查看群白名单",
      /添加私聊白名单 卡池名 用户QQ   允许该用户私聊使用",
      /移除私聊白名单 卡池名 用户QQ   取消该用户的私聊权限",
      /查看私聊白名单 卡池名         查看私聊白名单",
      /卡池地址                   查看卡池根目录路径
      /卡池索引                   查看所有卡池文件名和展示名对应关系
      /gachahelpadm             查看所有指令

    ==================== 行为说明 ====================
    - 白名单为空：该卡池对所有群开放
    - 白名单非空：只有名单内的群能抽卡，其他群不可用
    - 抽卡次数按 群聊+用户+卡池+日期 记录，跨天自动重置
    - 卡片目录内如有多个 png，只取一个，其余忽略并提示
    """

    def __init__(self, context: Context, config: AstrBotConfig=None):
        super().__init__(context)
        self.config = config

        self.data_dir = StarTools.get_data_dir("gacha")
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.pool_dir = os.path.join(self.data_dir, "pool")
        os.makedirs(self.pool_dir, exist_ok=True)
        self.config_path = os.path.join(self.data_dir, "config.json")

        self.records_path = os.path.join(self.data_dir, "records.json")

        self.index_path = os.path.join(self.data_dir, "pool_index.json")
        self.pool_index = self._load_index()
        self._migrate_index()

        self._init_global_config()
        self.global_settings = self._load_global_config()
        self.default_daily_limit = self._validate_limit(
            self.global_settings.get("default_daily_limit", 1)
        )

        self.records = self._load_records()
        # 编辑模式
        self.edit_lock_path = os.path.join(self.data_dir, "edit_lock.json")
        self.edit_lock = self._load_edit_lock()
        # 进程重启后内存 draft 已丢失，锁残留则自动清掉
        if self.edit_lock.get("locked"):
            self.edit_lock["locked"] = False
            self.edit_lock["locked_at"] = 0
            self._save_edit_lock()
        # 抽卡全局锁：保证演出（before/after）不被并发抽卡打断
        self._draw_lock = asyncio.Lock()

        self.edit_draft = None          # 当前编辑卡池的 draft
        self.edit_original = None       # 进入编辑时的快照（撤回用）
        self.edit_commands = []         # 命令历史
        self.editing_pool = ""          # 正在编辑的卡池文件夹名

        self.pools = {}
        self._scan_pools()

        self._cleanup_running = False

        # 启动时清理一次旧记录
        try:
            self._cleanup_old_records()
        except Exception as e:
            logger.error(f"[抽卡] 启动清理失败: {e}")

        logger.info(f"[抽卡] 插件初始化完成，卡池目录: {self.pool_dir}")
        logger.info(f"[抽卡] 已扫描 {len(self.pools)} 个卡池: {list(self.pools.keys())}")

        self._register_web_apis()

    # ==================== 校验工具 ====================

    def _validate_limit(self, value) -> int:
        if isinstance(value, bool):
            return 1
        if isinstance(value, int) and value > 0:
            return value
        return 1

    def _validate_weight(self, value) -> int:
        if isinstance(value, bool):
            return 50
        if isinstance(value, (int, float)):
            w = int(value)
            return w if w > 0 else 50
        return 50

    # ==================== 全局配置 ====================
    def _load_index(self) -> dict:
        if not os.path.exists(self.index_path):
            return {}
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"[抽卡] 读取 pool_index.json 失败: {e}")
            return {}

    def _save_index(self):
        try:
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(self.pool_index, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[抽卡] 保存 pool_index.json 失败: {e}")

    def _init_global_config(self):
        if os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({"default_daily_limit": 1}, f, ensure_ascii=False, indent=4)
            logger.info("[抽卡] 已创建默认 config.json")
        except Exception as e:
            logger.error(f"[抽卡] 创建 config.json 失败: {e}")

    def _load_global_config(self) -> dict:
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[抽卡] 读取 config.json 失败: {e}")
            return {}

    # ==================== 卡池配置读取 ====================

    def _load_pool_json(self, pool_path: str) -> dict:
        """读取 pool.json，缺失字段从全局默认补全"""
        path = os.path.join(pool_path, "pool.json")
        raw = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                logger.error(f"[抽卡] 读取 pool.json 失败 {path}: {e}")
        if not isinstance(raw, dict):
            raw = {}
        wl=raw.get("group_whitelist", [])
        if not isinstance(wl, list):
            wl=[]

        pwl = raw.get("private_whitelist", [])
        if not isinstance(pwl, list):
            pwl = []

        return {
            "display_name": str(raw.get("display_name", os.path.basename(pool_path))),
            "daily_limit": self._validate_limit(raw.get("daily_limit", self.default_daily_limit)),
            "group_whitelist": [str(g) for g in wl if str(g).strip()],
            "private_whitelist": [str(g) for g in pwl if str(g).strip()],
        }

    def _ensure_weights_json(self, pool_path: str, cards: list) -> dict:
        """
        确保 weights.json 存在，首次生成时所有卡权重默认为 50
        已存在时只补全缺失的卡，不覆盖用户已改的权重。
        返回最终的权重字典。
        """
        path = os.path.join(pool_path, "weights.json")
        weights = {}

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    weights = loaded
            except Exception as e:
                logger.error(f"[抽卡] 读取 weights.json 失败 {path}: {e}")

        # 补全缺失卡（不覆盖已有值）
        updated = False
        for card in cards:
            if card not in weights:
                weights[card] = 50
                updated = True

        # 首次生成或补全后写回
        if updated or not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(weights, f, ensure_ascii=False, indent=4)
                logger.info(f"[抽卡] 已更新 weights.json: {path}")
            except Exception as e:
                logger.error(f"[抽卡] 写入 weights.json 失败 {path}: {e}")

        return weights

    # ==================== 卡池扫描 ====================

    def _scan_pools(self,pool_name: str=""):
        #扫描全部卡池或单个
        """重新扫描 pool 目录，每个卡池独立读取 pool.json 和 weights.json"""
        if not pool_name:
            self.pools = {}
            if not os.path.isdir(self.pool_dir):
                return
            targets = [e for e in os.listdir(self.pool_dir)
                       if os.path.isdir(os.path.join(self.pool_dir, e))]
        else:
            # 单卡池：先从内存移除，再重新扫描
            self.pools.pop(pool_name, None)
            pool_path = os.path.join(self.pool_dir, pool_name)
            if not os.path.isdir(pool_path):
                return
            targets = [pool_name]

        for entry in targets:
            pool_path = os.path.join(self.pool_dir, entry)
            cfg = self._load_pool_json(pool_path)
            cards_dir = os.path.join(pool_path, "cards")

            cards = []
            if os.path.isdir(cards_dir):
                for item in os.listdir(cards_dir):
                    if os.path.isdir(os.path.join(cards_dir, item)):
                        cards.append(item)
            if entry not in self.pool_index:
                display = cfg.get("display_name", entry)
                self.pool_index[entry] = display
                self._save_index()
            display = self.pool_index.get(entry, entry)
            weights = self._ensure_weights_json(pool_path, cards)
            self.pools[entry] = {
                "display_name": display,
                "daily_limit": cfg["daily_limit"],
                "group_whitelist": cfg["group_whitelist"],
                "private_whitelist": cfg["private_whitelist"],
                "weights": weights,
                "cards": cards
            }
    def _scan_pools_light(self) -> dict:
        """
        轻量扫描：只读 pool.json + 列 cards/，
        不读 weights.json、不写盘、不更新 self.pools。
        仅供 /查看卡池 使用。
        """
        result = {}
        if not os.path.isdir(self.pool_dir):
            return result

        for entry in os.listdir(self.pool_dir):
            pool_path = os.path.join(self.pool_dir, entry)
            if not os.path.isdir(pool_path):
                continue

            cfg = self._load_pool_json(pool_path)

            cards = []
            cards_dir = os.path.join(pool_path, "cards")
            if os.path.isdir(cards_dir):
                for item in os.listdir(cards_dir):
                    if os.path.isdir(os.path.join(cards_dir, item)):
                        cards.append(item)

            display = self.pool_index.get(entry, cfg.get("display_name", entry))
            result[entry] = {
                "display_name": display,
                "daily_limit": cfg["daily_limit"],
                "group_whitelist": cfg["group_whitelist"],
                "private_whitelist": cfg["private_whitelist"],
                "cards": cards,
            }

        return result

    # ==================== 资源路径 ====================

    def _get_card_resources(self, card_dir: str):
        """
        读取第一个图片png
        严格读取desc.txt
        返回：(img_path or None, description or "", warning or "")
        """
        if not os.path.isdir(card_dir):
            return None,None, ""

        img_path = None
        desc_path = None
        img_extra = False

        with os.scandir(card_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                name = entry.name.lower()
                if name.endswith(".png"):
                    if img_path is None:
                        img_path = entry.path
                    else:
                        img_extra = True
                elif entry.name == "desc.txt":
                    desc_path = entry.path

        description = None
        if desc_path:
            try:
                with open(desc_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        description = content
            except Exception as e:
                logger.error(f"[抽卡] 读取描述失败 {card_dir}: {e}")

        warnings = ""
        if img_extra:
            warnings="⚠️ 多张图，仅取第一张"
        return img_path, description, warnings
#读取额外文本
    def _read_optional(self,card_dir: str,filename: str):
        path = os.path.join(card_dir, filename)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else None
        except Exception as e:
            logger.error(f"[抽卡] 读取 {filename}失败: {e}")
            return None
    # ==================== 权重构建 ====================

    def _build_weights(self, pool_info: dict, cards: list) -> list:
        """根据 weights.json 构建权重列表，非法值自动降级为 50"""
        raw = pool_info.get("weights", {})
        weights = []
        for card in cards:
            w = 50
            for key in (card, card + ".png", card + ".txt"):
                if key in raw:
                    w = self._validate_weight(raw[key])
                    break
            weights.append(w)
        return weights

    # ==================== 抽卡记录 ====================

    def _load_records(self) -> dict:
        if not os.path.exists(self.records_path):
            return {}
        try:
            with open(self.records_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[抽卡] 读取 records.json 失败: {e}")
            return {}

    def _save_records(self):
        try:
            with open(self.records_path, "w", encoding="utf-8") as f:
                json.dump(self.records, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[抽卡] 保存 records.json 失败: {e}")

    def _cleanup_old_records(self):
        """保留今天+昨天，其余 key 全部删除。"""
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        keep = {str(today), str(yesterday)}

        to_del = []
        for key in self.records:
            if "_" not in key:
                to_del.append(key)
                continue
            date_str = key.rsplit("_", 1)[-1]
            if date_str not in keep:
                to_del.append(key)

        if not to_del:
            return

        for k in to_del:
            del self.records[k]
        self._save_records()
        logger.info(f"[抽卡] 清理旧记录 {len(to_del)} 条")

    async def _cleanup_old_records_task(self):
        """UI 触发的后台清理，不阻塞响应"""
        if self._cleanup_running:
            return
        self._cleanup_running = True
        try:
            await asyncio.sleep(0)  # 让 response 先发出去
            self._cleanup_old_records()
        except Exception as e:
            logger.error(f"[抽卡] 后台清理失败: {e}")
        finally:
            self._cleanup_running = False

    def _get_today_count(self, group_id: str, user_id: str, pool_name: str) -> int:
        today = str(datetime.date.today())
        record = self.records.get(f"{group_id}_{user_id}_{pool_name}_{today}")
        if isinstance(record, dict):
            return record.get("count", 0)
        if isinstance(record, int):
            return record
        return 0

    def _increment_today_count(self, group_id: str, user_id: str, pool_name: str, card_name: str = ""):
        today = str(datetime.date.today())
        key = f"{group_id}_{user_id}_{pool_name}_{today}"
        record = self.records.get(key)
        if isinstance(record, int):
            # 兼容旧版：整数次数 → 新版字典
            record = {"count": record, "cards": []}
        elif not isinstance(record, dict):
            record = {"count": 0, "cards": []}
        record["count"] += 1
        if card_name:
            record["cards"].append(card_name)
        self.records[key] = record
        self._save_records()

    # ==================== 抽卡指令 ====================

    @filter.command("抽卡")
    async def draw(self, event: AstrMessageEvent, display_pool_name: str = ""):
        """抽卡指令：/抽卡 卡池名（全局锁串行，防止并发抽卡导致演出交错）"""
        async with self._draw_lock:
            user_id = event.get_sender_id()
            group_id = event.get_group_id() or "0"
            display_pool_name = display_pool_name.strip()
            folder_name = self.resolve_pool(display_pool_name)

            if not display_pool_name:
                yield event.plain_result("请输入卡池名称，可以使用 /查看卡池")
                return
            if not folder_name:
                available = self._get_available_pools(event)
                if not available:
                    yield event.plain_result("当前没有可用的卡池。")
                    return
                pool_list = "、".join(info["display_name"] for info in available)
                yield event.plain_result(f"卡池「{display_pool_name}」不存在。")
                yield event.plain_result(f"可用卡池：{pool_list}\n用法：/抽卡 卡池名")
                return

            ok, msg = self.name_check(folder_name)
            if not ok:
                yield event.plain_result("卡池" + msg)
                return

            if folder_name not in self.pools:
                self._scan_pools(folder_name)
                if folder_name not in self.pools:
                    yield event.plain_result(f"卡池「{display_pool_name}」无效。")
                    return

            pool_info = self.pools[folder_name]

            # 管理员跳过白名单
            if not event.is_admin():
                raw_gid = event.get_group_id()
                if raw_gid:
                    if not self.group_allowed(folder_name, str(raw_gid)):
                        yield event.plain_result(f"卡池「{display_pool_name}」无效。")
                        return
                else:
                    pwl = pool_info.get("private_whitelist", [])
                    if str(user_id) not in pwl:
                        yield event.plain_result(f"卡池「{display_pool_name}」暂不开放私聊。")
                        return

            cards = pool_info["cards"]
            daily_limit = pool_info["daily_limit"]

            today_count = self._get_today_count(group_id, user_id, folder_name)
            if today_count >= daily_limit:
                yield event.plain_result(
                    f"今日在「{display_pool_name}」已抽 {today_count}/{daily_limit} 次，明天再来。"
                )
                return
            if not cards:
                yield event.plain_result(f"卡池「{display_pool_name}」还没有卡片。")
                return

            weights = self._build_weights(pool_info, cards)
            picked = random.choices(cards, weights=weights, k=1)[0]

            card_dir = os.path.join(self.pool_dir, folder_name, "cards", picked)
            img_path, description, warning = self._get_card_resources(card_dir)

            announce_text = self._read_optional(card_dir, "announce.txt")
            if not announce_text:
                pool_path = os.path.join(self.pool_dir, folder_name)
                announce_text = self._read_optional(pool_path, "default_announce.txt") or ""

            before_text = self._read_optional(card_dir, "before.txt")
            after_text = self._read_optional(card_dir, "after.txt")

            if before_text:
                yield event.plain_result(before_text)
                await asyncio.sleep(1)

            display_name = self._get_card_display(folder_name, picked)

            # announce 行：有 announce 就带前缀，否则只显示卡名
            if announce_text:
                announce_line = f"{announce_text}「{display_name}」！"
            else:
                announce_line = f"「{display_name}」！"

            # 尾部文本
            tail_lines = []
            if description:
                tail_lines.append(description)
            tail_lines.append(f"今日已抽：{today_count + 1}/{daily_limit}")
            if warning:
                tail_lines.append(warning)
            tail = "\n\n".join(tail_lines)

            # 组装一条 chain：@ + announce + 图片 + tail
            chain = [
                Comp.At(qq=user_id),
                Comp.Plain("\n" + announce_line),
            ]
            if img_path:
                chain.append(Comp.Image(file=img_path, url=img_path))
            if tail:
                chain.append(Comp.Plain(tail))

            yield event.chain_result(chain)

            if after_text:
                await asyncio.sleep(0.5)
                yield event.plain_result(after_text)

            self._increment_today_count(group_id, user_id, folder_name, picked)
            today_count = self._get_today_count(group_id, user_id, folder_name)

            logger.info(f"[抽卡] 用户 {user_id} 在「{folder_name}」抽到了 {picked}")
            
    # ==================== 新建卡池 ====================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("新建卡池")
    async def create_pool(self, event: AstrMessageEvent, pool_name: str = "",display_pool_name: str = ""):
        """新建卡池：/新建卡池 卡池名 [展示名]"""
        pool_name = pool_name.strip()
        display_pool_name = display_pool_name.strip()
        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法： /新建卡池 卡池名 [展示名]<33字符")
            return
        ok, msg = self.name_check(pool_name)
        if not ok:
            yield event.plain_result("卡池" + msg)
            return
        # 冲突检查
        conflict = self._name_conflict(pool_name)
        if conflict:
            yield event.plain_result(f"卡池名冲突：{conflict}")
            return
        if self.pool_exists(pool_name) > 0:
            yield event.plain_result(f"卡池「{pool_name}」已存在。")
            return

        if display_pool_name:
            if len(display_pool_name) > 32:
                yield event.plain_result("显示名不能超过 32 个字符")
                return
            ok2, msg2 = self.name_check(display_pool_name)
            if not ok2:
                yield event.plain_result("卡池展示" + msg2)
                return
            conflict2 = self._name_conflict(display_pool_name)
            if conflict2:
                yield event.plain_result(f"卡池展示名冲突：{conflict2}")
                return
        try:
            pool_path = os.path.join(self.pool_dir, pool_name)
            os.makedirs(os.path.join(pool_path, "cards"), exist_ok=True)

            with open(os.path.join(pool_path, "pool.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "display_name": pool_name,
                    "daily_limit": 1,
                    "group_whitelist": [],
                    "private_whitelist": []
                }, f, ensure_ascii=False, indent=4)
            with open(os.path.join(pool_path, "default_announce.txt"), "w", encoding="utf-8") as f:
                f.write("恭喜你抽到了——")
            if display_pool_name:
                self.pool_index[pool_name] = display_pool_name
            else:
                self.pool_index[pool_name] = pool_name
            self._save_index()

            self._scan_pools(pool_name)

            logger.info(f"[抽卡] {event.get_sender_id()} 新建卡池 {pool_name}")
            yield event.plain_result(
                f"✅ 卡池「{pool_name}」已创建。\n"
                f"用 /添加卡片 {pool_name} 卡片名 （描述） 来添加卡片。"
            )
        except Exception as e:
            logger.error(f"[抽卡] 新建卡池失败: {e}")
            yield event.plain_result(f"新建失败：{e}")

    #新增卡片
    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("添加卡片")
    async def add_card(self, event: AstrMessageEvent):
        """新建卡池指令：/添加卡片 卡池名 卡片名 （描述<300）

        仅管理员可用。

        """
        msg = self._extract_args(event, "添加卡片")
        parts = msg.split(maxsplit=2)
        if len(parts) < 1:
            yield event.plain_result( "用法：/添加卡片 卡池名 卡片名 [描述]<300字")
            return
        if len(parts) == 1:
            yield event.plain_result( "请输入添加卡片名称\n用法：/添加卡片 卡池名 卡片名 [描述]<300字")
            return
        pool_name = parts[0].strip()
        card_name = parts[1].strip()
        desc_str = parts[2].strip() if len(parts) >= 3 else ""

        if len(desc_str) > 299:
            yield event.plain_result("你有病啊写那么多字")
            return
        # 检查卡池名称合法性
        ok, msg = self.name_check(pool_name)
        if not ok:
            yield event.plain_result('卡池' + msg)
            return
        # 匹配切换展示名称/实际名称
        valid_name = self.resolve_pool(pool_name)
        if not valid_name:
            yield event.plain_result("卡池不存在")
            return
        pool_name = valid_name
        # 检查卡片名称合法性
        ok, msg = self.name_check(card_name)
        if not ok:
            yield event.plain_result('卡片' + msg)
            return
        # 卡片查重
        if self.card_exists(pool_name, card_name) > 0:
            yield event.plain_result(f"卡片「{card_name}」已存在，请换个名字。")
            return

        # 创建卡片
        try:
            card_path = os.path.join(self.pool_dir, pool_name, "cards", card_name)
            os.makedirs(card_path, exist_ok=True)
            # 写入描述
            with open(os.path.join(card_path, "desc.txt"), "w", encoding="utf-8") as f:
                if desc_str:
                    f.write(desc_str)
            for fname in ("announce.txt", "name.txt","before.txt", "after.txt"):
                fpath = os.path.join(card_path, fname)
                if not os.path.exists(fpath):
                    open(fpath, "w", encoding="utf-8").close()
            self._scan_pools(pool_name)
            yield event.plain_result(f"✅ 卡片「{card_name}」已添加到「{pool_name}」")

        except Exception as e:
            logger.error(f"[抽卡] 添加卡片失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

    # ==================== 辅助指令 ====================

    @filter.command("查看卡池")
    async def list_pools(self, event: AstrMessageEvent):
        """查看当前群可用的卡池"""
        pools = self._scan_pools_light()
        available = self._get_available_pools(event,pools)
        if not available:
            yield event.plain_result("当前没有可用的卡池。")
            return
        lines = ["📋 可用卡池列表："]
        for info in available:
            lines.append(
                f"  • {info['display_name']}"
                f"- {len(info['cards'])} 张卡 / 每日 {info['daily_limit']} 次"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("查看次数")
    async def my_status(self, event: AstrMessageEvent, display_pool_name: str = ""):
        """查询今日抽卡剩余次数"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id() or "0"

        display_pool_name = display_pool_name.strip()

        self._scan_pools()
        if display_pool_name:
            folder_name = self.resolve_by_display(display_pool_name)
            if not folder_name or folder_name not in self.pools:
                yield event.plain_result(f"卡池「{display_pool_name}」不存在。")
                return
            limit = self.pools[folder_name]["daily_limit"]
            count = self._get_today_count(group_id, user_id, folder_name)
            yield event.plain_result(f"卡池「{display_pool_name}」今日已抽：{count}/{limit}")
        else:
            if not self.pools:
                yield event.plain_result("还没有任何卡池。")
                return
            lines = ["📊 今日抽卡状态："]
            for folder, info in self.pools.items():
                count = self._get_today_count(group_id, user_id, folder)
                lines.append(f"  • {info['display_name']}：{count}/{info['daily_limit']}")
            yield event.plain_result("\n".join(lines))

    async def terminate(self):
        logger.info("[抽卡] 插件已卸载")

    @filter.command("抽卡记录")
    async def draw_history(self, event: AstrMessageEvent):
        """查看自己今日抽卡记录：/抽卡记录"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id() or "0"
        today = str(datetime.date.today())
        prefix = f"{group_id}_{user_id}_"
        suffix = f"_{today}"

        # 先过滤出属于当前用户的键，再排序
        user_keys = [
            k for k in self.records.keys()
            if k.startswith(prefix) and k.endswith(suffix)
        ]
        user_keys.sort()

        lines = []
        for key in user_keys:
            folder = key[len(prefix):len(key) - len(suffix)]
            display = self.pool_index.get(folder, folder)
            record = self.records[key]
            cards = record.get("cards", []) if isinstance(record, dict) else []
            for i, card in enumerate(cards, 1):
                card_display = self._get_card_display(folder, card)
                lines.append(f"{display}-{i}-{card_display}")

        if not lines:
            yield event.plain_result("你今天还没有抽卡记录。")
            return

        yield event.plain_result("📜 今日抽卡记录：\n" + "\n".join(lines))

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("卡池地址")
    async def show_pool_path(self, event: AstrMessageEvent):
        """查看卡池文件夹路径：/卡池地址"""
        yield event.plain_result(f"卡池目录：{self.pool_dir}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("卡池索引")
    async def show_pool_index(self, event: AstrMessageEvent):
        """查看卡池索引：/卡池索引"""
        if not self.pool_index:
            yield event.plain_result("当前没有任何卡池索引。")
            return

        lines = ["📇 卡池索引（文件夹名 - 展示名）："]
        for folder in sorted(self.pool_index.keys()):
            display = self.pool_index[folder]
            lines.append(f"  {folder} - {display}")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("编辑卡片")
    async def edit_card(self, event: AstrMessageEvent):
        """编辑卡片文本：/编辑卡片 卡池名 卡名 desc|announce|name|before|after [内容]"""
        msg =self._extract_args(event,"编辑卡片")
        parts = msg.split(maxsplit=3)
        if len(parts) < 3:
            yield event.plain_result(
                "用法：/编辑卡片 卡池名 卡名 desc|announce|name|before|after [内容]\n"
                "  desc：卡片描述\n"
                "  announce：抽卡提示词（默认「恭喜你抽到了」）\n"
                "  name：展示名称（无则展示文件夹名称）\n"
                "  before：抽卡图片前的单独一段话\n"
                "  after：抽卡文本后的单独一段话\n"
                "不带内容则清空该字段。"
            )
            return

        pool_name = parts[0].strip()
        card_name = parts[1].strip()
        field = parts[2].strip()
        content = parts[3].strip() if len(parts) >= 4 else ""
        #合法检查
        if field not in ("desc", "announce", "name", "before", "after"):
            yield event.plain_result("字段只能是 desc / announce / name / before / after")
            return
        #卡池展示名称-文件夹名称索引
        valid_name = self.resolve_pool(pool_name)
        if not valid_name:
            yield event.plain_result("卡池不存在")
            return
        pool_name = valid_name
        #存在判断
        ok, err = self.both_check(pool_name, card_name)
        if not ok:
            yield event.plain_result(err)
            return
        #获取地址
        card_dir = os.path.join(self.pool_dir, pool_name,"cards",card_name)
        path = os.path.join(card_dir,f"{field}.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                if content:
                    f.write(content)
            if content:
                yield event.plain_result(f"✅ 已设置「{pool_name}/{card_name}」的 {field}")
            else:
                yield event.plain_result(f"✅ 已清空「{pool_name}/{card_name}」的 {field}")
        except Exception as e:
            logger.error(f"[抽卡] 卡片编辑失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

        # ==================== 群白名单管理 ====================

    def _save_group_whitelist(self, pool_name: str, wl: list) -> bool:
            """把群白名单写回 pool.json，并刷新缓存"""
            path = os.path.join(self.pool_dir, pool_name, "pool.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                raw["group_whitelist"] = wl
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(raw, f, ensure_ascii=False, indent=4)
                self._scan_pools(pool_name)
                return True
            except Exception as e:
                logger.error(f"[抽卡] 群白名单写入失败: {e}")
                return False

    def _save_private_whitelist(self, pool_name: str, wl: list) -> bool:
        """把私聊白名单写回 pool.json，并刷新缓存"""
        path = os.path.join(self.pool_dir, pool_name, "pool.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw["private_whitelist"] = wl
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=4)
            self._scan_pools(pool_name)
            return True
        except Exception as e:
            logger.error(f"[抽卡] 私聊白名单写入失败: {e}")
            return False

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("添加白名单")
    async def add_group_whitelist(self, event: AstrMessageEvent,pool_name: str = "", group_id: str = ""):
            """添加群到白名单：/添加白名单 卡池名 群号"""
            pool_name = pool_name.strip()
            group_id = group_id.strip()
            if not pool_name:
                yield event.plain_result("请输入卡池名称\n用法：/添加白名单 卡池名 群号")
                return
            # 卡池展示名称-文件夹名称索引
            pool_name = self.resolve_pool(pool_name)
            if self.pool_exists(pool_name) < 2:
                yield event.plain_result("卡池不存在")
                return

            if not group_id:
                yield event.plain_result("请输入群号\n用法：/添加白名单 卡池名 群号\n'0'表示私聊")
                return

            if not group_id.isdigit():
                yield event.plain_result("群号必须是纯数字")
                return

            wl = list(self.pools[pool_name].get("group_whitelist", []))
            if group_id in wl:
                yield event.plain_result(f"群 {group_id} 已在「{pool_name}」的白名单中。")
                return

            wl.append(group_id)
            if self._save_group_whitelist(pool_name, wl):
                yield event.plain_result(f"✅ 已添加群 {group_id} 到「{pool_name}」的白名单。")
            else:
                yield event.plain_result("写入失败，请检查日志。")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("移除白名单")
    async def remove_group_whitelist(self, event: AstrMessageEvent,
                                         pool_name: str = "", group_id: str = ""):
            """从白名单移除群：/移除白名单 卡池名 群号"""
            pool_name = pool_name.strip()
            group_id = group_id.strip()
            if not pool_name:
                yield event.plain_result("请输入卡池名称\n用法：/添加白名单 卡池名 群号")
                return
            # 卡池展示名称-文件夹名称索引
            pool_name = self.resolve_pool(pool_name)
            if self.pool_exists(pool_name) < 2:
                yield event.plain_result("卡池不存在")
                return

            if not group_id:
                yield event.plain_result("请输入对应群号\n用法：/添加白名单 卡池名 群号")
                return

            wl = list(self.pools[pool_name].get("group_whitelist", []))
            if group_id not in wl:
                yield event.plain_result(f"群 {group_id} 不在「{pool_name}」的白名单中。")
                return

            wl.remove(group_id)
            if self._save_group_whitelist(pool_name, wl):
                yield event.plain_result(f"✅ 已从「{pool_name}」的白名单移除群 {group_id}。")
            else:
                yield event.plain_result("写入失败，请检查日志。")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("查看白名单")
    async def show_group_whitelist(self, event: AstrMessageEvent, pool_name: str = ""):
            """查看卡池群白名单：/查看白名单 卡池名"""
            pool_name = pool_name.strip()
            if not pool_name:
                yield event.plain_result("请输入卡池名称\n用法：/查看白名单 卡池名")
                return
            pool_name = self.resolve_pool(pool_name)
            if self.pool_exists(pool_name) < 2:
                yield event.plain_result("卡池不存在")
                return

            wl = self.pools[pool_name].get("group_whitelist", [])
            if not wl:
                yield event.plain_result(f"卡池「{pool_name}」群白名单为空（对所有群开放）。")
                return
            yield event.plain_result(f"卡池「{pool_name}」群白名单：\n" + "\n".join(f"  • {g}" for g in wl))

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("添加私聊白名单")
    async def add_private_whitelist(self, event: AstrMessageEvent, pool_name: str = "", user_id: str = ""):
        """添加用户到私聊白名单：/添加私聊白名单 卡池名 用户QQ"""
        pool_name = pool_name.strip()
        user_id = user_id.strip()

        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/添加私聊白名单 卡池名 用户QQ")
            return
        pool_name = self.resolve_pool(pool_name)
        if self.pool_exists(pool_name) < 2:
            yield event.plain_result("卡池不存在")
            return

        if not user_id:
            yield event.plain_result("请输入用户QQ\n用法：/添加私聊白名单 卡池名 用户QQ")
            return

        if not user_id.isdigit():
            yield event.plain_result("用户QQ必须是纯数字")
            return

        wl = list(self.pools[pool_name].get("private_whitelist", []))
        if user_id in wl:
            yield event.plain_result(f"用户 {user_id} 已在「{pool_name}」的私聊白名单中。")
            return

        wl.append(user_id)
        if self._save_private_whitelist(pool_name, wl):
            yield event.plain_result(f"✅ 已添加用户 {user_id} 到「{pool_name}」的私聊白名单。")
        else:
            yield event.plain_result("写入失败，请检查日志。")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("移除私聊白名单")
    async def remove_private_whitelist(self, event: AstrMessageEvent, pool_name: str = "", user_id: str = ""):
        """从私聊白名单移除用户：/移除私聊白名单 卡池名 用户QQ"""
        pool_name = pool_name.strip()
        user_id = user_id.strip()

        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/移除私聊白名单 卡池名 用户QQ")
            return
        pool_name = self.resolve_pool(pool_name)
        if self.pool_exists(pool_name) < 2:
            yield event.plain_result("卡池不存在")
            return

        if not user_id:
            yield event.plain_result("请输入用户QQ\n用法：/移除私聊白名单 卡池名 用户QQ")
            return

        wl = list(self.pools[pool_name].get("private_whitelist", []))
        if user_id not in wl:
            yield event.plain_result(f"用户 {user_id} 不在「{pool_name}」的私聊白名单中。")
            return

        wl.remove(user_id)
        if self._save_private_whitelist(pool_name, wl):
            yield event.plain_result(f"✅ 已从「{pool_name}」的私聊白名单移除用户 {user_id}。")
        else:
            yield event.plain_result("写入失败，请检查日志。")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("查看私聊白名单")
    async def show_private_whitelist(self, event: AstrMessageEvent, pool_name: str = ""):
        """查看卡池私聊白名单：/查看私聊白名单 卡池名"""
        pool_name = pool_name.strip()
        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/查看私聊白名单 卡池名")
            return
        pool_name = self.resolve_pool(pool_name)
        if self.pool_exists(pool_name) < 2:
            yield event.plain_result("卡池不存在")
            return

        wl = self.pools[pool_name].get("private_whitelist", [])
        if not wl:
            yield event.plain_result(f"卡池「{pool_name}」私聊白名单为空（禁止所有私聊使用）。")
            return
        yield event.plain_result(
            f"卡池「{pool_name}」私聊白名单：\n" + "\n".join(f"  • {g}" for g in wl)
        )

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("设置权重")
    async def set_weight(self, event: AstrMessageEvent,pool_name: str = "", card_name: str = "", weight_str: str = ""):
            """设置卡片权重：/设置权重 卡池名 卡片名 权重"""
            pool_name = pool_name.strip()
            card_name = card_name.strip()
            weight_str = weight_str.strip()
            if not pool_name:
                yield event.plain_result("请输入卡池名称\n用法： /设置权重 卡池名 卡片名 权重<1001")
                return
            if not card_name:
                yield event.plain_result("请输入卡片名称\n用法： /设置权重 卡池名 卡片名 权重<1001")
                return
            if not weight_str:
                yield event.plain_result("请输入权重\n用法： /设置权重 卡池名 卡片名 权重<1001")
                return

            pool_name = self.resolve_pool(pool_name)
            ok, msg = self.both_check(pool_name, card_name)
            if not ok:
                yield event.plain_result(msg)
                return

            # 权重解析
            try:
                w = int(weight_str)
                if w <= 0:
                    yield event.plain_result("权重必须是正整数")
                    return
                if w > 1000:
                    yield event.plain_result("权重必须小于等于1000")
                    return
            except ValueError:
                yield event.plain_result("权重必须是小于等于1000的正整数")
                return

            path = os.path.join(self.pool_dir, pool_name, "weights.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    weights = json.load(f)
                weights[card_name] = w
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(weights, f, ensure_ascii=False, indent=4)

                self._scan_pools(pool_name)

                yield event.plain_result(f"✅ 卡片「{card_name}」在「{pool_name}」的权重已设为 {w}")
            except Exception as e:
                logger.error(f"[抽卡] 设置权重失败: {e}")
                yield event.plain_result(f"操作失败：{e}")

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("查看权重")
    async def show_weights(self, event: AstrMessageEvent, pool_name: str = ""):
            pool_name = pool_name.strip()
            if not pool_name:
                yield event.plain_result("用法：/查看权重 卡池名")
                return
            pool_name = self.resolve_pool(pool_name)
            if self.pool_exists(pool_name) < 2:
                yield event.plain_result("卡池不存在")
                return

            weights = self.pools[pool_name].get("weights", {})
            validated = {k: self._validate_weight(v) for k, v in weights.items()}
            total = sum(validated.values()) or 1
            lines = [f"📊 卡池「{pool_name}」权重："]
            for card, w in validated.items():
                pct = w / total * 100
                lines.append(f"  • {card}：{w}（{pct:.1f}%）")
            yield event.plain_result("\n".join(lines))
    # ==================== 删除卡片 ====================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删除卡片")
    async def delete_card(self, event: AstrMessageEvent,
                          pool_name: str = "", card_name: str = ""):
        """删除卡片：/删除卡片 卡池名 卡名"""
        pool_name = pool_name.strip()
        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/删除卡片 卡池名 卡名")
            return
        pool_name = self.resolve_pool(pool_name)
        ok, msg = self.both_check(pool_name, card_name)
        if not ok:
            yield event.plain_result(msg)
            return

        try:
            import shutil
            card_dir = os.path.join(self.pool_dir, pool_name, "cards", card_name)
            shutil.rmtree(card_dir)

            # 顺手清掉 weights.json 里的记录
            weights_path = os.path.join(self.pool_dir, pool_name, "weights.json")
            if os.path.exists(weights_path):
                with open(weights_path, "r", encoding="utf-8") as f:
                    weights = json.load(f)
                weights.pop(card_name, None)
                with open(weights_path, "w", encoding="utf-8") as f:
                    json.dump(weights, f, ensure_ascii=False, indent=4)

            self._scan_pools(pool_name)
            yield event.plain_result(f"✅ 已删除卡片「{card_name}」")
        except Exception as e:
            logger.error(f"[抽卡] 删除卡片失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

    # ==================== 删除卡池 ====================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("删除卡池")
    async def delete_pool(self, event: AstrMessageEvent, pool_name: str = ""):
        """删除整个卡池：/删除卡池 卡池名"""
        pool_name = pool_name.strip()
        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/删除卡池 卡池名")
            return
        pool_name = self.resolve_pool(pool_name)
        if not pool_name:
            yield event.plain_result("卡池名称无效")
            return
        if self.pool_exists(pool_name) < 1:
            yield event.plain_result("卡池不存在")
            return

        try:
            import shutil
            pool_path = os.path.join(self.pool_dir, pool_name)
            shutil.rmtree(pool_path)
            self.pool_index.pop(pool_name, None)
            self._save_index()

            self._scan_pools()  # 全量刷新，从内存里移除
            yield event.plain_result(f"✅ 已删除卡池「{pool_name}」")
        except Exception as e:
            logger.error(f"[抽卡] 删除卡池失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

    # ==================== 修改卡池展示名称 ====================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("设置显示名")
    async def set_display_name(self, event: AstrMessageEvent):
        """修改卡池展示名：/设置显示名 卡池名 新显示名"""
        msg = self._extract_args(event, "设置显示名")
        parts = msg.split(maxsplit=1)
        if len(parts) < 1:
            yield event.plain_result("用法：/设置显示名 卡池名 新显示名")
            return
        if len(parts) < 2:
            yield event.plain_result("请输入新的展示名称\n用法：/设置显示名 卡池名 新显示名")
            return

        pool_input = parts[0].strip()
        display_name = parts[1].strip()

        if not display_name or not pool_input:
            yield event.plain_result("用法：/设置显示名 卡池名 新显示名")
            return

        folder = self.resolve_pool(pool_input)
        if not folder:
            yield event.plain_result(f"卡池「{pool_input}」不存在。")
            return

        if len(display_name) > 32:
            yield event.plain_result("显示名不能超过 32 个字符")
            return

        # 冲突检查，排除自己
        conflict = self._name_conflict(display_name, exclude_folder=folder)
        if conflict:
            yield event.plain_result(f"展示名冲突：{conflict}")
            return

        self.pool_index[folder] = display_name
        self._save_index()
        self._scan_pools(folder)
        logger.info(f"[抽卡] {event.get_sender_id()} 设置 {folder} 显示名为 {display_name}")
        yield event.plain_result(f"✅ 卡池「{folder}」显示名已设为「{display_name}」")

    # ==================== 修改每日次数上限 ====================

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("设置次数")
    async def set_daily_limit(self, event: AstrMessageEvent,pool_name: str = "", limit_str: str = ""):
        """修改卡池每日抽卡上限：/设置次数 卡池名 次数"""
        pool_name = pool_name.strip()
        limit_str = limit_str.strip()

        if not pool_name:
            yield event.plain_result("请输入卡池名称\n用法：/设置次数 卡池名 次数")
            return
        if not limit_str:
            yield event.plain_result("请输入限制次数\n用法：/设置次数 卡池名 次数")
            return
        pool_name = self.resolve_pool(pool_name)
        if self.pool_exists(pool_name) < 1:
            yield event.plain_result("卡池不存在")
            return

        try:
            limit = int(limit_str)
            if limit <= 0:
                yield event.plain_result("次数必须是正整数")
                return
        except ValueError:
            yield event.plain_result("次数必须是正整数")
            return

        path = os.path.join(self.pool_dir, pool_name, "pool.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw["daily_limit"] = limit
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=4)

            self._scan_pools(pool_name)
            logger.info(f"[抽卡] {event.get_sender_id()} 设置 {pool_name} 每日上限为 {limit}")
            yield event.plain_result(f"✅ 卡池「{pool_name}」每日上限已设为 {limit}")
        except Exception as e:
            logger.error(f"[抽卡] 设置每日上限失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

        ##工具函数#########################################

    #索引：用户
    def resolve_by_display(self, user_input: str) -> str :
        """按展示名找文件夹名"""
        for folder, display in self.pool_index.items():
            if display == user_input:
                return folder
        return ""
    #索引：admin
    def resolve_pool(self, name: str) -> str:
        """
        把用户/管理员输入的名称解析成文件夹名。
        优先级：
          1. 精确匹配文件夹名
          2. 匹配某个卡池的展示名
          3. 都不中 → None
        """
        name = name.strip()
        if not name:
            return ""

        # 1. 文件夹名优先
        if name in self.pools:
            return name
        # 兜底：内存里没有但磁盘上有（未扫描到的空卡池）
        if os.path.isdir(os.path.join(self.pool_dir, name)):
            return name

        # 2. 匹配展示名
        for folder, display in self.pool_index.items():
            if display == name:
                return folder

        return ""

    def _migrate_index(self):
        """首次启动时，把 pool.json 里的 display_name 迁到 pool_index.json"""
        if os.path.exists(self.index_path):
            return

        index = {}
        if os.path.isdir(self.pool_dir):
            for entry in os.listdir(self.pool_dir):
                pool_path = os.path.join(self.pool_dir, entry)
                if not os.path.isdir(pool_path):
                    continue

                display = entry
                pool_json = os.path.join(pool_path, "pool.json")
                if os.path.exists(pool_json):
                    try:
                        with open(pool_json, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                            if isinstance(raw, dict):
                                display = str(raw.get("display_name", entry))
                    except Exception as e:
                        logger.error(f"[抽卡] 迁移索引读取 {pool_json} 失败: {e}")

                index[entry] = display

        self.pool_index = index
        self._save_index()
        logger.info(f"[抽卡] 索引迁移完成，共 {len(index)} 个卡池")
        
    def _extract_args(self, event: AstrMessageEvent, cmd: str) -> str:
        """只提取用户输入的纯文本，跳过引用消息，清理各类标记"""
        import re

        texts = []
        for comp in event.message_obj.message:
            if "reply" in type(comp).__name__.lower():
                continue
            if isinstance(comp, Comp.Plain):
                texts.append(comp.text)

        msg = "".join(texts).strip()

        # 清除 [MSG_ID:数字] 和 [CQ:任意内容]
        msg = re.sub(r"\[(?:MSG_ID:\d+|CQ:[^\]]*)\]", "", msg).strip()

        # 剥离命令前缀
        for prefix in (f"/{cmd}", cmd):
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()
                break

        return msg
    #检查群白名单
    def group_allowed(self, pool_name: str, group_id: str) -> bool:
            """群白名单为空 = 无限制"""
            pool_info = self.pools.get(pool_name)
            if not pool_info:
                return False
            wl = pool_info.get("group_whitelist", [])
            if not wl:
                return True
            return str(group_id) in wl

        #名称检查
    def name_check(self,name) -> tuple:
            if not name:
                return False, "名称无效"
            if name in (".", "..") or name.startswith("."):
                return False, "名称不能以点号开头，也不能是 . 或 .."
            invalid_chars = set('\\/:*?"<>|_')
            if any(c in invalid_chars for c in name):
                return False,'名称不能包含以下字符：\\ / : * ? " < > |_'
            if len(name) > 32:
                return False,"名称不能超过 32 个字符。"
            return True,""

        #检查卡池存在/重复
    def pool_exists(self,pool_name: str) -> int:
            if not pool_name:
                return 0
            ans=0
            if os.path.exists(os.path.join(self.pool_dir,pool_name)):
                ans+=1
            if pool_name in self.pools:
                ans+=1
            return ans
        #检查卡片存在/重复
    def card_exists(self,pool_name: str,card_name: str) -> int:
            if not pool_name or not card_name:
                return 0
            ans=0
            card_dir = os.path.join(self.pool_dir, pool_name,"cards",card_name)
            if os.path.exists(card_dir):
                ans+=1
            pool_info=self.pools.get(pool_name)
            if pool_info and card_name in pool_info.get("cards",[]):
                ans+=1
            return ans
        #卡池卡片合法性以及存在检查
    def both_check(self,pool_name: str,card_name: str) -> tuple:
            ok,msg = self.name_check(pool_name)
            if not ok: return False, "卡池"+msg
            ok,msg = self.name_check(card_name)
            if not ok: return False, "卡片"+msg
            if self.pool_exists(pool_name)<1:
                return False,"卡池不存在"
            if self.card_exists(pool_name,card_name)<2:
                return False,"卡片不存在"
            return True,""

    #set名称冲突检查
    def _name_conflict(self, name: str, exclude_folder: str = "") -> str | None:
        """
        检查 name 是否与任何文件夹名或展示名冲突。
        exclude_folder：设置展示名时，排除自己所在的文件夹。
        返回冲突说明，无冲突返回 None。
        """
        # 1. 与文件夹名冲突（排除自己）
        if name != exclude_folder:
            if name in self.pools:
                return f"与现有文件夹名「{name}」冲突"
            if os.path.isdir(os.path.join(self.pool_dir, name)):
                return f"与现有文件夹名「{name}」冲突"

        # 2. 与展示名冲突（排除自己）
        for folder, display in self.pool_index.items():
            if display == name and folder != exclude_folder:
                return f"与卡池「{folder}」的展示名冲突"

        return None

    def _get_card_display(self, pool_folder: str, card_folder: str) -> str:
        name_path = os.path.join(self.pool_dir, pool_folder, "cards", card_folder, "name.txt")
        if os.path.exists(name_path):
            try:
                with open(name_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
            except Exception as e:
                logger.error(f"[抽卡] 读取 name.txt 失败 {name_path}: {e}")
        return card_folder

    def _get_available_pools(self, event: AstrMessageEvent, pools: dict = None) -> list:
        """
        返回当前会话可用的卡池信息列表。
        pools 未传 → 用 self.pools（默认行为）
        pools 传入 → 用传入的（例如轻量扫描结果）
        """
        source = pools if pools is not None else self.pools
        is_admin = event.is_admin()
        raw_gid = event.get_group_id()
        user_id = str(event.get_sender_id())
        available = []
        for folder, info in source.items():
            if not info["cards"]:
                continue
            if not is_admin:
                if raw_gid:
                    wl = info.get("group_whitelist", [])
                    if wl and str(raw_gid) not in wl:
                        continue
                else:
                    pwl = info.get("private_whitelist", [])
                    if user_id not in pwl:
                        continue
            available.append(info)
        return available

    # ==================== 编辑模式 ====================

    def _load_edit_lock(self) -> dict:
        default = {"locked": False, "locked_at": 0}
        if not os.path.exists(self.edit_lock_path):
            return default
        try:
            with open(self.edit_lock_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {
                        "locked": bool(data.get("locked", False)),
                        "locked_at": float(data.get("locked_at", 0)),
                    }
        except Exception as e:
            logger.error(f"[抽卡] 读取 edit_lock.json 失败: {e}")
        return default

    def _save_edit_lock(self):
        try:
            with open(self.edit_lock_path, "w", encoding="utf-8") as f:
                json.dump(self.edit_lock, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"[抽卡] 保存 edit_lock.json 失败: {e}")

    def _load_pool_to_draft(self, folder: str) -> bool:
        info = self.pools.get(folder)
        if not info:
            return False

        pool_path = os.path.join(self.pool_dir, folder)

        pool_json = {}
        pj = os.path.join(pool_path, "pool.json")
        if os.path.exists(pj):
            try:
                with open(pj, "r", encoding="utf-8") as f:
                    pool_json = json.load(f) or {}
            except Exception:
                pass

        card_texts = {}
        for card in info["cards"]:
            card_dir = os.path.join(pool_path, "cards", card)
            texts = {}
            for field in ("desc", "name", "announce", "before", "after"):
                txt_path = os.path.join(card_dir, f"{field}.txt")
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, "r", encoding="utf-8") as f:
                            texts[field] = f.read()
                    except Exception:
                        texts[field] = ""
                else:
                    texts[field] = ""
            card_texts[card] = texts
        default_announce = ""
        da_path = os.path.join(pool_path, "default_announce.txt")
        if os.path.exists(da_path):
            try:
                with open(da_path, "r", encoding="utf-8") as f:
                    default_announce = f.read().strip()
            except Exception:
                default_announce = ""
        draft = {
            "folder": folder,
            "display_name": str(pool_json.get("display_name", folder)),
            "daily_limit": self._validate_limit(pool_json.get("daily_limit", self.default_daily_limit)),
            "group_whitelist": [str(g) for g in pool_json.get("group_whitelist", []) if str(g).strip()],
            "private_whitelist": [str(g) for g in pool_json.get("private_whitelist", []) if str(g).strip()],
            "default_announce": default_announce,
            "weights": dict(info.get("weights", {})),
            "cards": list(info["cards"]),
            "card_texts": card_texts,
            "new_cards": {},
            "deleted_cards": [],
            "card_images": {},
        }
        self.edit_draft = draft
        self.edit_original = copy.deepcopy(draft)
        self.edit_commands = []
        self.editing_pool = folder
        return True

    def _apply_command_to_draft(self, cmd: dict) -> tuple:
        if not self.edit_draft:
            return False, "没有正在编辑的卡池"

        op = cmd.get("op", "")
        draft = self.edit_draft

        if op == "card_field":
            card = str(cmd.get("card", ""))
            field = str(cmd.get("field", ""))
            content = str(cmd.get("content", ""))
            if field not in ("desc", "name", "announce", "before", "after"):
                return False, "无效字段"
            if card in draft["deleted_cards"]:
                return False, "卡片已删除"
            if card not in draft["cards"] and card not in draft["new_cards"]:
                return False, "卡片不存在"
            if card not in draft["card_texts"]:
                draft["card_texts"][card] = {}
            draft["card_texts"][card][field] = content
            return True, "ok"

        elif op == "pool_field":
            field = str(cmd.get("field", ""))
            content = cmd.get("content")
            if field == "display_name":
                draft["display_name"] = str(content)[:32]
            elif field == "daily_limit":
                draft["daily_limit"] = self._validate_limit(content)
            elif field == "group_whitelist":
                if isinstance(content, list):
                    draft["group_whitelist"] = [str(g) for g in content if str(g).strip()]
            elif field == "private_whitelist":
                if isinstance(content, list):
                    draft["private_whitelist"] = [str(g) for g in content if str(g).strip()]
            elif field == "default_announce":
                draft["default_announce"] = str(content)[:50]
            else:
                return False, "无效字段"
            return True, "ok"

        elif op == "weight":
            card = str(cmd.get("card", ""))
            weight = cmd.get("weight", 50)
            if card not in draft["cards"] and card not in draft["new_cards"]:
                return False, "卡片不存在"
            draft["weights"][card] = self._validate_weight(weight)
            return True, "ok"

        elif op == "add_card":
            card = str(cmd.get("card", "")).strip()
            ok, msg = self.name_check(card)
            if not ok:
                return False, msg
            if card in draft["cards"] or card in draft["new_cards"]:
                return False, "卡片已存在"
            if card in draft["deleted_cards"]:
                draft["deleted_cards"].remove(card)
            draft["new_cards"][card] = {
                "desc": "", "name": "", "announce": "", "before": "", "after": ""
            }
            draft["weights"][card] = 50
            return True, "ok"

        elif op == "delete_card":
            card = str(cmd.get("card", ""))
            if card in draft["new_cards"]:
                del draft["new_cards"][card]
                draft["weights"].pop(card, None)
            elif card in draft["cards"]:
                draft["cards"].remove(card)
                draft["deleted_cards"].append(card)
                draft["card_texts"].pop(card, None)
            else:
                return False, "卡片不存在"
            draft["card_images"].pop(card, None)
            return True, "ok"

        elif op == "set_card_image":
            card = str(cmd.get("card", ""))
            filename = str(cmd.get("filename", "image.png")).strip() or "image.png"
            data = str(cmd.get("data", ""))
            if card not in draft["cards"] and card not in draft["new_cards"]:
                return False, "卡片不存在"
            if not data:
                return False, "图片数据为空"

            try:
                raw = base64.b64decode(data)
            except Exception:
                return False, "图片数据格式错误"

            if len(raw) > 5 * 1024 * 1024:
                return False, "图片不能超过 5MB"
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                return False, "只支持 PNG 格式"

            if not filename.lower().endswith(".png"):
                if "." in filename:
                    filename = filename.rsplit(".", 1)[0] + ".png"
                else:
                    filename += ".png"

            draft["card_images"][card] = {"filename": filename, "data": data}
            return True, "ok"

        elif op == "delete_card_image":
            card = str(cmd.get("card", ""))
            if card not in draft["cards"] and card not in draft["new_cards"]:
                return False, "卡片不存在"
            draft["card_images"][card] = {"deleted": True}
            return True, "ok"

        return False, "未知命令"

    def _rebuild_draft(self):
        if not self.edit_original:
            return
        self.edit_draft = copy.deepcopy(self.edit_original)
        for cmd in self.edit_commands:
            self._apply_command_to_draft(cmd)

    def _save_draft_to_disk(self) -> tuple:
        if not self.edit_draft:
            return False, "没有可保存的卡池"

        draft = self.edit_draft
        folder = draft["folder"]
        pool_path = os.path.join(self.pool_dir, folder)

        try:
            import shutil

            pool_json = {
                "display_name": draft["display_name"],
                "daily_limit": draft["daily_limit"],
                "group_whitelist": draft["group_whitelist"],
                "private_whitelist": draft.get("private_whitelist", []),
            }
            with open(os.path.join(pool_path, "pool.json"), "w", encoding="utf-8") as f:
                json.dump(pool_json, f, ensure_ascii=False, indent=4)
            with open(os.path.join(pool_path, "default_announce.txt"), "w", encoding="utf-8") as f:
                f.write(draft.get("default_announce", "") or "")

            for card, texts in draft["card_texts"].items():
                if card in draft["deleted_cards"]:
                    continue
                card_dir = os.path.join(pool_path, "cards", card)
                os.makedirs(card_dir, exist_ok=True)
                for field in ("desc", "name", "announce", "before", "after"):
                    content = texts.get(field, "")
                    txt_path = os.path.join(card_dir, f"{field}.txt")
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(content or "")

            for card, texts in draft["new_cards"].items():
                card_dir = os.path.join(pool_path, "cards", card)
                os.makedirs(card_dir, exist_ok=True)
                for field in ("desc", "name", "announce", "before", "after"):
                    content = texts.get(field, "")
                    txt_path = os.path.join(card_dir, f"{field}.txt")
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(content or "")

            for card in draft["deleted_cards"]:
                card_dir = os.path.join(pool_path, "cards", card)
                if os.path.isdir(card_dir):
                    shutil.rmtree(card_dir)
                draft["weights"].pop(card, None)

            # 图片处理
            for card, img_info in draft.get("card_images", {}).items():
                if card in draft["deleted_cards"]:
                    continue
                card_dir = os.path.join(pool_path, "cards", card)
                os.makedirs(card_dir, exist_ok=True)

                if img_info.get("deleted"):
                    for f in os.listdir(card_dir):
                        if f.lower().endswith(".png"):
                            try:
                                os.remove(os.path.join(card_dir, f))
                            except Exception as e:
                                logger.error(f"[抽卡] 删除图片失败: {e}")
                else:
                    for f in os.listdir(card_dir):
                        if f.lower().endswith(".png"):
                            try:
                                os.remove(os.path.join(card_dir, f))
                            except Exception as e:
                                logger.error(f"[抽卡] 删除旧图失败: {e}")

                    try:
                        raw = base64.b64decode(img_info["data"])
                        fname = img_info.get("filename", "image.png")
                        if not fname.lower().endswith(".png"):
                            fname += ".png"
                        with open(os.path.join(card_dir, fname), "wb") as f:
                            f.write(raw)
                    except Exception as e:
                        logger.error(f"[抽卡] 写图片失败: {e}")

            with open(os.path.join(pool_path, "weights.json"), "w", encoding="utf-8") as f:
                json.dump(draft["weights"], f, ensure_ascii=False, indent=4)

            self._scan_pools(folder)

            logger.info(f"[抽卡] 保存编辑草稿到磁盘: {folder}")
            return True, "已保存"
        except Exception as e:
            logger.error(f"[抽卡] 保存 draft 失败: {e}")
            return False, str(e)

    # ==================== 帮助 ====================

    @filter.command("抽卡帮助")
    async def show_help(self, event: AstrMessageEvent):
        """用户帮助：/抽卡帮助"""
        lines = [
            "📋 抽卡插件 · 用户指令",
            "",
            "/抽卡 卡池名 · 抽一次卡",
            "/查看卡池 · 列出所有卡池",
            "/查看次数 [卡池名] · 查看今日抽卡次数",
            "/抽卡记录 · 查看自己今日抽卡结果",
            "/抽卡帮助 · 显示本帮助",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(PermissionType.ADMIN)
    @filter.command("gachahelpadm")
    async def gacha_cmd(self, event: AstrMessageEvent):
        """管理员帮助：/gachahelpadm"""
        lines = [
            "🔐 抽卡插件 · 完整指令",
            "",
            "【用户指令】",
            "/抽卡 卡池名 · 抽一次卡",
            "/查看卡池 · 列出所有卡池",
            "/查看次数 [卡池名] · 查看今日抽卡次数",
            "/抽卡记录 · 查看自己今日抽卡结果",
            "/抽卡帮助 · 显示用户帮助",
            "",
            "【管理员指令】",
            "/新建卡池 卡池名 · 创建空卡池",
            "/设置显示名 卡池名 新名 · 修改显示名",
            "/设置次数 卡池名 次数 · 修改每日上限",
            "/添加卡片 卡池名 卡名 [描述] · 添加卡片",
            "/编辑卡片 卡池名 卡名 announce|name|desc|before|after 内容 \n   自定义卡片文本",
            "/删除卡片 卡池名 卡名 · 删除卡片",
            "/删除卡池 卡池名 · 删除整个卡池",
            "/设置权重 卡池名 卡名 权重 · 修改卡片权重",
            "/查看权重 卡池名 · 查看权重分布",
            "/添加白名单 卡池名 群号 · 添加群到白名单",
            "/移除白名单 卡池名 群号 · 从白名单移除",
            "/查看白名单 卡池名 · 查看群白名单",
            "/添加私聊白名单 卡池名 用户QQ · 允许该用户私聊使用",
            "/移除私聊白名单 卡池名 用户QQ · 取消该用户的私聊权限",
            "/查看私聊白名单 卡池名 · 查看私聊白名单",
            "/卡池地址 · 查看数据目录",
            "/卡池索引 · 查看文件夹名与展示名的对应关系",
        ]
        yield event.plain_result("\n".join(lines))

#-------------------------------------------------------------
    # ==================== Web UI API ====================

    def _register_web_apis(self):
        """注册 UI 需要的后端接口"""
        from astrbot.api.web import request, json_response, error_response
        import base64

        PLUGIN_ID = "astrbot_plugin_gacha"

        async def api_pools():
            pools = []
            for folder, info in self.pools.items():
                if folder == self.editing_pool and self.edit_draft:
                    d = self.edit_draft
                    card_count = len(d["cards"]) + len(d["new_cards"])
                    display = d["display_name"]
                else:
                    card_count = len(info["cards"])
                    display = info["display_name"]
                pools.append({
                    "folder": folder,
                    "display": display,
                    "card_count": card_count,
                })
            return json_response({"pools": pools})

        async def api_cards():
            pool = request.query.get("pool") or ""

            if pool == self.editing_pool and self.edit_draft:
                d = self.edit_draft
                cards = []
                for folder in d["cards"]:
                    if folder in d["deleted_cards"]:
                        continue
                    texts = d["card_texts"].get(folder, {})
                    display = (texts.get("name") or "").strip() or folder
                    cards.append({"folder": folder, "display": display})
                for folder, texts in d["new_cards"].items():
                    display = (texts.get("name") or "").strip() or folder
                    cards.append({"folder": folder, "display": display})
                return json_response({"cards": cards})

            info = self.pools.get(pool)
            if not info:
                return error_response("卡池不存在")
            cards = []
            for folder in info["cards"]:
                cards.append({
                    "folder": folder,
                    "display": self._get_card_display(pool, folder),
                })
            return json_response({"cards": cards})

        async def api_card_detail():
            pool = request.query.get("pool") or ""
            card = request.query.get("card") or ""
            if not pool or not card:
                return error_response("参数缺失")

            if pool == self.editing_pool and self.edit_draft:
                d = self.edit_draft
                if card in d["deleted_cards"]:
                    return error_response("卡片不存在")
                texts = d["card_texts"].get(card) or d["new_cards"].get(card)
                if not texts:
                    return error_response("卡片不存在")

                img_b64 = None
                img_info = d.get("card_images", {}).get(card)
                if img_info is not None:
                    if not img_info.get("deleted"):
                        img_b64 = img_info.get("data")
                else:
                    card_dir = os.path.join(self.pool_dir, pool, "cards", card)
                    if os.path.isdir(card_dir):
                        try:
                            with os.scandir(card_dir) as it:
                                for entry in it:
                                    if entry.is_file() and entry.name.lower().endswith(".png"):
                                        with open(entry.path, "rb") as f:
                                            img_b64 = base64.b64encode(f.read()).decode("ascii")
                                        break
                        except Exception:
                            pass

                display = (texts.get("name") or "").strip() or card
                return json_response({
                    "card": card,
                    "display": display,
                    "image": img_b64,
                    "desc": texts.get("desc", ""),
                    "announce": texts.get("announce", ""),
                    "name": texts.get("name", ""),
                    "before": texts.get("before", ""),
                    "after": texts.get("after", ""),
                })

            card_dir = os.path.join(self.pool_dir, pool, "cards", card)
            if not os.path.isdir(card_dir):
                return error_response("卡片不存在")

            img_b64 = None
            try:
                with os.scandir(card_dir) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.lower().endswith(".png"):
                            with open(entry.path, "rb") as f:
                                img_b64 = base64.b64encode(f.read()).decode("ascii")
                            break
            except Exception as e:
                logger.error(f"[抽卡] UI 读取图片失败: {e}")

            def read_txt(name):
                p = os.path.join(card_dir, name)
                if not os.path.exists(p):
                    return ""
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read().strip()
                except Exception:
                    return ""

            return json_response({
                "card": card,
                "display": self._get_card_display(pool, card),
                "image": img_b64,
                "desc": read_txt("desc.txt"),
                "announce": read_txt("announce.txt"),
                "name": read_txt("name.txt"),
                "before": read_txt("before.txt"),
                "after": read_txt("after.txt"),
            })

        async def api_pool_json():
            pool = request.query.get("pool") or ""

            if pool == self.editing_pool and self.edit_draft:
                d = self.edit_draft
                return json_response({
                    "display": d["display_name"],
                    "pool_json": {
                        "display_name": d["display_name"],
                        "daily_limit": d["daily_limit"],
                        "group_whitelist": d["group_whitelist"],
                        "private_whitelist": d.get("private_whitelist", []),
                        "default_announce": d.get("default_announce", ""),
                    },
                    "weights": d["weights"],
                })

            info = self.pools.get(pool)
            if not info:
                return error_response("卡池不存在")

            pool_path = os.path.join(self.pool_dir, pool)

            pool_json = {}
            pj = os.path.join(pool_path, "pool.json")
            if os.path.exists(pj):
                try:
                    with open(pj, "r", encoding="utf-8") as f:
                        pool_json = json.load(f)
                except Exception:
                    pass

            weights = {}
            wj = os.path.join(pool_path, "weights.json")
            if os.path.exists(wj):
                try:
                    with open(wj, "r", encoding="utf-8") as f:
                        weights = json.load(f)
                except Exception:
                    pass

            da_path = os.path.join(pool_path, "default_announce.txt")
            default_announce = ""
            if os.path.exists(da_path):
                try:
                    with open(da_path, "r", encoding="utf-8") as f:
                        default_announce = f.read().strip()
                except Exception:
                    default_announce = ""
            pool_json["default_announce"] = default_announce

            return json_response({
                "display": info["display_name"],
                "pool_json": pool_json,
                "weights": weights,
            })

        # 5. 模拟抽卡（不记录、不扣次数、不走白名单）
        async def api_simulate():
            pool = request.query.get("pool") or ""
            info = self.pools.get(pool)
            if not info:
                return error_response("卡池不存在")

            cards = info["cards"]
            if not cards:
                return error_response("卡池还没有卡片")

            weights = self._build_weights(info, cards)
            picked = random.choices(cards, weights=weights, k=1)[0]

            card_dir = os.path.join(self.pool_dir, pool, "cards", picked)

            # 读图片
            image_b64 = None
            try:
                with os.scandir(card_dir) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.lower().endswith(".png"):
                            with open(entry.path, "rb") as f:
                                image_b64 = base64.b64encode(f.read()).decode("ascii")
                            break
            except Exception as e:
                logger.error(f"[抽卡] 模拟读图失败: {e}")

            def read_txt(name):
                p = os.path.join(card_dir, name)
                if not os.path.exists(p):
                    return ""
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return f.read().strip()
                except Exception:
                    return ""

            return json_response({
                "card": picked,
                "image": image_b64,
                "desc": read_txt("desc.txt"),
                "announce": read_txt("announce.txt"),
                "name": read_txt("name.txt"),
                "before": read_txt("before.txt"),
                "after": read_txt("after.txt"),
            })

        # 6. 卡片缩略图
        async def api_card_thumbnail():
            pool = request.query.get("pool") or ""
            card = request.query.get("card") or ""
            if not pool or not card:
                return error_response("参数缺失")

            card_dir = os.path.join(self.pool_dir, pool, "cards", card)
            if not os.path.isdir(card_dir):
                return error_response("卡片不存在")

            img_path = None
            try:
                with os.scandir(card_dir) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.lower().endswith(".png"):
                            img_path = entry.path
                            break
            except Exception as e:
                logger.error(f"[抽卡] 缩略图扫描失败: {e}")

            if not img_path:
                return error_response("无图片")

            # 图片过大直接跳过（避免列表加载爆炸）
            try:
                if os.path.getsize(img_path) > 500 * 1024:
                    return error_response("图片过大")
                with open(img_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                return json_response({"thumbnail": data})
            except Exception as e:
                logger.error(f"[抽卡] 缩略图读取失败: {e}")
                return error_response("读取失败")

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/thumbnail", api_card_thumbnail, ["GET"], "卡片缩略图")

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/simulate", api_simulate, ["GET"], "模拟抽卡")

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/pools", api_pools, ["GET"], "获取卡池列表")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/cards", api_cards, ["GET"], "获取卡片列表")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/card", api_card_detail, ["GET"], "获取卡片详情")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/pool_json", api_pool_json, ["GET"], "获取卡池 JSON")
        # ============ 新建卡池（编辑模式用，直接写盘） ============
        async def api_create_pool():
            data = await request.json(default={})
            pool_name = str(data.get("pool_name", "")).strip()

            ok, msg = self.name_check(pool_name)
            if not ok:
                return error_response(msg)

            if self.pool_exists(pool_name) > 0:
                return error_response(f"卡池「{pool_name}」已存在")

            # 与文件夹名 / 展示名冲突检查
            conflict = self._name_conflict(pool_name)
            if conflict:
                return error_response(f"卡池名冲突：{conflict}")

            try:
                pool_path = os.path.join(self.pool_dir, pool_name)
                os.makedirs(os.path.join(pool_path, "cards"), exist_ok=True)

                with open(os.path.join(pool_path, "pool.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "display_name": pool_name,
                        "daily_limit": 1,
                        "group_whitelist": [],
                        "private_whitelist": []
                    }, f, ensure_ascii=False, indent=4)
                with open(os.path.join(pool_path, "default_announce.txt"), "w", encoding="utf-8") as f:
                    f.write("恭喜你抽到了——")

                self.pool_index[pool_name] = pool_name
                self._save_index()
                self._scan_pools(pool_name)

                logger.info(f"[抽卡] WebUI 新建卡池 {pool_name}")
                return json_response({"ok": True})
            except Exception as e:
                logger.error(f"[抽卡] 新建卡池失败: {e}")
                return error_response(str(e))

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/pool/create", api_create_pool, ["POST"], "新建卡池")
        # ============ 删除卡池（编辑模式用，直接删盘） ============
        async def api_delete_pool():
            data = await request.json(default={})
            folder = str(data.get("pool", "")).strip()

            if not folder:
                return error_response("缺少 pool 参数")
            if self.pool_exists(folder) < 1:
                return error_response("卡池不存在")

            try:
                import shutil
                pool_path = os.path.join(self.pool_dir, folder)
                shutil.rmtree(pool_path)

                # 若正在编辑的正是被删的卡池，顺手清掉 draft
                if self.editing_pool == folder:
                    self.edit_draft = None
                    self.edit_original = None
                    self.edit_commands = []
                    self.editing_pool = ""

                self.pool_index.pop(folder, None)
                self._save_index()
                self._scan_pools()
                logger.info(f"[抽卡] WebUI 删除卡池 {folder}")
                return json_response({"ok": True})
            except Exception as e:
                logger.error(f"[抽卡] 删除卡池失败: {e}")
                return error_response(str(e))

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/pool/delete", api_delete_pool, ["POST"], "删除卡池")
        # ============ 编辑模式 ============
        async def api_edit_status():
            # 先构造响应，让 UI 先展开
            result = json_response({
                "locked": self.edit_lock.get("locked", False),
                "editing_pool": self.editing_pool or "",
                "can_undo": len(self.edit_commands) > 0,
            })
            # 后台调度清理，不阻塞本次响应
            asyncio.create_task(self._cleanup_old_records_task())
            return result

        async def api_edit_enter():
            if self.edit_lock.get("locked"):
                return error_response("编辑模式已开启")
            self.edit_lock["locked"] = True
            self.edit_lock["locked_at"] = time.time()
            self._save_edit_lock()
            return json_response({"ok": True})

        async def api_edit_exit():
            self.edit_draft = None
            self.edit_original = None
            self.edit_commands = []
            self.editing_pool = ""
            self.edit_lock["locked"] = False
            self._save_edit_lock()
            return json_response({"ok": True})

        async def api_edit_pool_load():
            if not self.edit_lock.get("locked"):
                return error_response("未进入编辑模式")
            # 兼容 POST body 和 query
            data = await request.json(default={})
            folder = data.get("pool") or request.query.get("pool") or ""
            if not self.pools.get(folder):
                return error_response("卡池不存在")
            if self.editing_pool:
                return error_response("请先结束当前卡池编辑")
            if not self._load_pool_to_draft(folder):
                return error_response("加载失败")
            return json_response({"ok": True})

        async def api_edit_pool_apply():
            if not self.edit_lock.get("locked") or not self.editing_pool:
                return error_response("未在编辑卡池")
            data = await request.json(default={})
            cmd = data.get("command", {})
            ok, msg = self._apply_command_to_draft(cmd)
            if not ok:
                return error_response(msg)
            self.edit_commands.append(cmd)
            return json_response({"ok": True})

        async def api_edit_pool_undo():
            if not self.edit_lock.get("locked") or not self.editing_pool:
                return error_response("未在编辑卡池")
            if not self.edit_commands:
                return error_response("没有可撤回的操作")
            self.edit_commands.pop()
            self._rebuild_draft()
            return json_response({"ok": True})

        async def api_edit_pool_save():
            if not self.edit_lock.get("locked") or not self.editing_pool:
                return error_response("未在编辑卡池")
            ok, msg = self._save_draft_to_disk()
            if not ok:
                return error_response(msg)
            self.edit_draft = None
            self.edit_original = None
            self.edit_commands = []
            self.editing_pool = ""
            return json_response({"ok": True})

        async def api_edit_pool_cancel():
            if not self.edit_lock.get("locked") or not self.editing_pool:
                return error_response("未在编辑卡池")
            self.edit_draft = None
            self.edit_original = None
            self.edit_commands = []
            self.editing_pool = ""
            return json_response({"ok": True})

        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/status", api_edit_status, ["GET"], "编辑状态")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/enter", api_edit_enter, ["POST"], "进入编辑模式")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/exit", api_edit_exit, ["POST"], "退出编辑模式")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/pool/load", api_edit_pool_load, ["POST"], "加载卡池到草稿")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/pool/apply", api_edit_pool_apply, ["POST"], "提交命令")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/pool/undo", api_edit_pool_undo, ["POST"], "撤回")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/pool/save", api_edit_pool_save, ["POST"], "保存草稿")
        self.context.register_web_api(
            f"/{PLUGIN_ID}/gacha/edit/pool/cancel", api_edit_pool_cancel, ["POST"], "取消编辑")

