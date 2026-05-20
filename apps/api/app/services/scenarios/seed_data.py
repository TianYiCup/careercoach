"""Canonical in-Python scenario catalog.

Single source of truth for both surfaces:
  * `GET /v1/scenarios` — listed and filtered by `ScenarioService`.
  * Session create / end — `scenario_seed.get_scenario_seed()` reads
    the same records to look up persona_title + opening_line.

When a DB-backed implementation lands, this module shrinks to the
seed-loader for the migration; the repository protocol stays the
same so callers don't change.

Covers all four `ScenarioCategory` literals so /v1/scenarios filter
tests have at least one match per band.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScenarioRecord:
    """All fields a `/v1/scenarios` row carries, plus the seed bits
    the session service needs at create + end time.

    Splitting `title` (used by both summary and seed) means the
    persona_title + opening_line live with the same identifier so a
    DB rename of the title only happens once.
    """

    id: str
    title: str
    category: str  # campus | jobhunt | intern | life — checked via Pydantic literal
    difficulty: int  # 1-5
    tags: tuple[str, ...]
    background: str
    real_user_certified: bool
    persona_title: str
    opening_line: str

    # Convenience so callers can do `record.scenario_title` to match the
    # PR-4a-vintage seed shape without renaming the canonical field.
    @property
    def scenario_title(self) -> str:
        return self.title


@dataclass(frozen=True)
class _FallbackRecord:
    """Used when an unknown `scenario_id` comes in — sprint-1 still
    wants the session create to succeed so the demo flow doesn't 404
    on a typo. Carries only the seed-side fields; never surfaced via
    `/v1/scenarios` listings.
    """

    title: str = "自由练习"
    persona_title: str = "陌生对手"
    opening_line: str = "我们来聊聊吧。"
    # Mirrored fields so `_FallbackRecord` can stand in for
    # `ScenarioRecord` at the seed lookup site.
    id: str = "sc_unknown"
    category: str = "life"
    difficulty: int = 2
    tags: tuple[str, ...] = field(default_factory=tuple)
    background: str = "未匹配到场景库中的条目，进入自由练习。"
    real_user_certified: bool = False

    @property
    def scenario_title(self) -> str:
        return self.title


FALLBACK_RECORD = _FallbackRecord()


# Forty scenarios spanning all four ScenarioCategory literals, meeting
# the PRD US-A1 L2 lower bounds (campus ≥ 12 / jobhunt ≥ 10 / intern
# ≥ 10 / life ≥ 8). `test_scenarios_repository` guards those counts so
# a future PR can't silently shrink the catalog below spec.
#
# The first three keep their PR 4a ids and copy so the MSW handlers
# (`apps/web/src/mocks/handlers/api.ts`) and any cached frontend
# fixtures continue to line up. Rows added after sc_007 carry
# `real_user_certified=False` — the ≥ 5-student certification
# (§3.0.5 D) is a separate content-ops pass, not a code change.
SCENARIO_CATALOG: tuple[ScenarioRecord, ...] = (
    ScenarioRecord(
        id="sc_001",
        title="周末加班谈判",
        category="intern",
        difficulty=3,
        tags=("拒绝", "上下级"),
        background="你刚结束周五的项目，老板在群里@你让周末加班赶进度。",
        real_user_certified=True,
        persona_title="强硬型 HR",
        opening_line="小林啊，这个周末项目得加个班，应该没问题吧？",
    ),
    ScenarioRecord(
        id="sc_002",
        title="实习转正薪资谈判",
        category="jobhunt",
        difficulty=4,
        tags=("薪资", "谈判"),
        background="实习期结束，HR 约你聊转正，薪资比你预期低 30%。",
        real_user_certified=True,
        persona_title="老 HR",
        opening_line="坐吧，转正的事情我们聊聊。你的期望薪资是多少？",
    ),
    ScenarioRecord(
        id="sc_003",
        title="室友深夜打游戏",
        category="campus",
        difficulty=2,
        tags=("室友", "沟通"),
        background="室友每天打游戏到凌晨 2 点，你明天有早八。",
        real_user_certified=False,
        persona_title="同寝室友",
        opening_line="嘿，再来一把？这把一定赢！",
    ),
    ScenarioRecord(
        id="sc_004",
        title="导师让无偿干私活",
        category="campus",
        difficulty=4,
        tags=("导师", "边界"),
        background="导师私下让你帮他做横向项目，没有任何报酬。",
        real_user_certified=False,
        persona_title="强势导师",
        opening_line="这个项目你来负责一下吧，对你以后申博也有帮助。",
    ),
    ScenarioRecord(
        id="sc_005",
        title="面试自我介绍",
        category="jobhunt",
        difficulty=1,
        tags=("面试", "自我介绍"),
        background="第一次校招面试，面试官让你做 2 分钟自我介绍。",
        real_user_certified=False,
        persona_title="资深面试官",
        opening_line="你好，先做一个 2 分钟的自我介绍吧。",
    ),
    ScenarioRecord(
        id="sc_006",
        title="父母催考公务员",
        category="life",
        difficulty=3,
        tags=("家人", "职业选择"),
        background="父母觉得稳定最重要，要你毕业就考公，你想做产品。",
        real_user_certified=False,
        persona_title="操心的母亲",
        opening_line="你看小明都考上了，你怎么还不抓紧准备？",
    ),
    ScenarioRecord(
        id="sc_007",
        title="房东恶意涨房租",
        category="life",
        difficulty=4,
        tags=("租房", "议价"),
        background="租约到期前一周，房东突然要涨 30% 房租。",
        real_user_certified=False,
        persona_title="精打细算的房东",
        opening_line="今年市场都涨了，明年房租按 1500 收吧。",
    ),
    # --- 校园 campus (sc_008–sc_017) ---
    ScenarioRecord(
        id="sc_008",
        title="催导师改论文初稿",
        category="campus",
        difficulty=3,
        tags=("导师", "拖延"),
        background="导师拖着不回你的论文修改意见，答辩快到了。",
        real_user_certified=False,
        persona_title="忙起来不回消息的导师",
        opening_line="最近会议多，你先按上次说的改着。",
    ),
    ScenarioRecord(
        id="sc_009",
        title="小组作业有人摆烂",
        category="campus",
        difficulty=3,
        tags=("小组作业", "边界"),
        background="小组大作业 deadline 在即，有个组员一直不干活。",
        real_user_certified=False,
        persona_title="摆烂的组员",
        opening_line="你们先做着，最后我来汇总，放心。",
    ),
    ScenarioRecord(
        id="sc_010",
        title="室友占用公共区",
        category="campus",
        difficulty=2,
        tags=("室友", "沟通"),
        background="室友长期把杂物堆在公共桌上，提醒了也不收。",
        real_user_certified=False,
        persona_title="不拘小节的室友",
        opening_line="啊？那桌子不一直这样吗，没影响吧。",
    ),
    ScenarioRecord(
        id="sc_011",
        title="社团换届竞选部长",
        category="campus",
        difficulty=4,
        tags=("社团", "竞争"),
        background="社团换届，你和另一人都想当部长，要当众答辩。",
        real_user_certified=False,
        persona_title="强势的竞争对手",
        opening_line="说实话，这个部长你来当，扛得住吗？",
    ),
    ScenarioRecord(
        id="sc_012",
        title="兼职老板拖欠工资",
        category="campus",
        difficulty=4,
        tags=("兼职", "维权"),
        background="奶茶店兼职做满一个月，老板找理由拖着不发工资。",
        real_user_certified=False,
        persona_title="油滑的兼职老板",
        opening_line="这个月生意不好，工资过阵子一起发哈。",
    ),
    ScenarioRecord(
        id="sc_013",
        title="申请奖学金答辩",
        category="campus",
        difficulty=3,
        tags=("奖学金", "答辩"),
        background="奖学金评定答辩，评委质疑你科研经历的含金量。",
        real_user_certified=False,
        persona_title="挑剔的评审老师",
        opening_line="你这个项目，是你自己做的，还是挂个名？",
    ),
    ScenarioRecord(
        id="sc_014",
        title="跟辅导员请长假",
        category="campus",
        difficulty=2,
        tags=("辅导员", "请假"),
        background="家里有事想请一周假，辅导员卡着不批。",
        real_user_certified=False,
        persona_title="按规矩办事的辅导员",
        opening_line="请假可以，可你这周有两节点名课，怎么办？",
    ),
    ScenarioRecord(
        id="sc_015",
        title="暂时还不上室友的钱",
        category="campus",
        difficulty=2,
        tags=("室友", "金钱"),
        background="你借了室友钱一时还不上，室友开始旁敲侧击。",
        real_user_certified=False,
        persona_title="不好意思直说的室友",
        opening_line="那个……上次那个事，你最近方便吗？",
    ),
    ScenarioRecord(
        id="sc_016",
        title="跟教授争取课程加签",
        category="campus",
        difficulty=3,
        tags=("选课", "争取"),
        background="一门必修课名额满了，你想找教授加签进去。",
        real_user_certified=False,
        persona_title="不想加人的教授",
        opening_line="这门课已经满了，你为什么非这学期上？",
    ),
    ScenarioRecord(
        id="sc_017",
        title="实验室师兄甩活",
        category="campus",
        difficulty=3,
        tags=("实验室", "边界"),
        background="实验室师兄总把自己的杂活推给你做。",
        real_user_certified=False,
        persona_title="爱使唤人的师兄",
        opening_line="师弟，这数据你帮我跑一下呗，你反正闲着。",
    ),
    # --- 求职 jobhunt (sc_018–sc_025) ---
    ScenarioRecord(
        id="sc_018",
        title="无领导小组讨论",
        category="jobhunt",
        difficulty=4,
        tags=("群面", "表达"),
        background="群面无领导讨论，有人一直抢话，你插不进去。",
        real_user_certified=False,
        persona_title="强势抢话的候选人",
        opening_line="这题答案很明显，我先说几点……",
    ),
    ScenarioRecord(
        id="sc_019",
        title="HR 说预算就这么多",
        category="jobhunt",
        difficulty=5,
        tags=("谈薪", "谈判"),
        background="终面谈薪，HR 说预算有限，给的数字低于你预期。",
        real_user_certified=False,
        persona_title="压价的 HR",
        opening_line="你的能力我们认可，但预算就这么多。",
    ),
    ScenarioRecord(
        id="sc_020",
        title="反问环节问福利",
        category="jobhunt",
        difficulty=2,
        tags=("面试", "反问"),
        background="面试反问环节，你想问加班和福利又怕显得功利。",
        real_user_certified=False,
        persona_title="面带微笑的面试官",
        opening_line="好，最后你有什么想问我的吗？",
    ),
    ScenarioRecord(
        id="sc_021",
        title="用 offer 争取加薪",
        category="jobhunt",
        difficulty=3,
        tags=("offer", "谈判"),
        background="你手握两个 offer，想用 B 公司争取 A 公司加薪。",
        real_user_certified=False,
        persona_title="不想被拿捏的 HR",
        opening_line="你说有别的 offer，那家具体什么条件？",
    ),
    ScenarioRecord(
        id="sc_022",
        title="offer 入职前被推迟",
        category="jobhunt",
        difficulty=3,
        tags=("offer", "维权"),
        background="签了 offer，入职前一周被通知岗位延期两个月。",
        real_user_certified=False,
        persona_title="一脸为难的 HR",
        opening_line="实在抱歉，编制有点变动，得请你再等等。",
    ),
    ScenarioRecord(
        id="sc_023",
        title="面试被问空窗期",
        category="jobhunt",
        difficulty=3,
        tags=("面试", "追问"),
        background="面试官追问你简历上半年的空窗期。",
        real_user_certified=False,
        persona_title="盯着简历的面试官",
        opening_line="我看你这里空着半年，这段时间在做什么？",
    ),
    ScenarioRecord(
        id="sc_024",
        title="群面被推选当汇报人",
        category="jobhunt",
        difficulty=3,
        tags=("群面", "边界"),
        background="群面讨论结束，组员把汇报的活推给没准备的你。",
        real_user_certified=False,
        persona_title="把活推出去的组员",
        opening_line="你思路最清楚，待会儿你来代表我们汇报。",
    ),
    ScenarioRecord(
        id="sc_025",
        title="宣讲会追问 HR",
        category="jobhunt",
        difficulty=2,
        tags=("校招", "沟通"),
        background="宣讲会后你想拦住 HR 问清楚岗位到底做什么。",
        real_user_certified=False,
        persona_title="赶时间的 HR",
        opening_line="同学不好意思，我等下有会，你长话短说。",
    ),
    # --- 实习 intern (sc_026–sc_034) ---
    ScenarioRecord(
        id="sc_026",
        title="实习只被安排打杂",
        category="intern",
        difficulty=3,
        tags=("实习", "边界"),
        background="实习两周，导师只让你贴发票、订会议室。",
        real_user_certified=False,
        persona_title="没空带你的实习导师",
        opening_line="你先把杂事熟悉一下，业务的事以后再说。",
    ),
    ScenarioRecord(
        id="sc_027",
        title="跨部门要数据被踢皮球",
        category="intern",
        difficulty=4,
        tags=("跨部门", "沟通"),
        background="你需要另一个组的数据，对接人一直说去找别人。",
        real_user_certified=False,
        persona_title="不想沾事的隔壁组同事",
        opening_line="这个不归我管，你去问你们组长？",
    ),
    ScenarioRecord(
        id="sc_028",
        title="汇报邮件被吐槽幼稚",
        category="intern",
        difficulty=2,
        tags=("职场", "反馈"),
        background="你发的工作汇报邮件被正职同事说太学生气。",
        real_user_certified=False,
        persona_title="直言不讳的正职同事",
        opening_line="你这邮件写得……有点像在交作业。",
    ),
    ScenarioRecord(
        id="sc_029",
        title="实习鉴定表想拿优秀",
        category="intern",
        difficulty=3,
        tags=("实习", "争取"),
        background="实习快结束，你想让导师在鉴定表上写优秀。",
        real_user_certified=False,
        persona_title="评价谨慎的实习导师",
        opening_line="鉴定表我一般都写「良好」，这是惯例。",
    ),
    ScenarioRecord(
        id="sc_030",
        title="被变相白嫖做超额活",
        category="intern",
        difficulty=4,
        tags=("实习", "边界"),
        background="导师让你做的活早超出实习生职责，还说锻炼你。",
        real_user_certified=False,
        persona_title="爱画饼的导师",
        opening_line="这项目交给你是看重你，做好了对转正有帮助。",
    ),
    ScenarioRecord(
        id="sc_031",
        title="跟正职同事破冰",
        category="intern",
        difficulty=1,
        tags=("职场", "沟通"),
        background="入职一周，午饭时你想跟组里正职同事搭上话。",
        real_user_certified=False,
        persona_title="有点高冷的正职同事",
        opening_line="（同事自顾自刷手机）哦，你也来吃饭啊。",
    ),
    ScenarioRecord(
        id="sc_032",
        title="想问转正名额被回避",
        category="intern",
        difficulty=4,
        tags=("转正", "沟通"),
        background="实习期满，你想问转正名额，导师总绕开话题。",
        real_user_certified=False,
        persona_title="不愿表态的导师",
        opening_line="转正的事公司还在定，你先做好手头的。",
    ),
    ScenarioRecord(
        id="sc_033",
        title="被同事甩锅",
        category="intern",
        difficulty=4,
        tags=("职场", "甩锅"),
        background="项目出了纰漏，同事把责任推到实习生你头上。",
        real_user_certified=False,
        persona_title="急着甩锅的同事",
        opening_line="这个环节不是你负责吗？我记得交给你了。",
    ),
    ScenarioRecord(
        id="sc_034",
        title="想准点下班怕被议论",
        category="intern",
        difficulty=3,
        tags=("职场", "边界"),
        background="活干完想准点走，但全组没人动，你不敢起身。",
        real_user_certified=False,
        persona_title="习惯加班的组长",
        opening_line="才六点，你们年轻人现在都这么准时啊？",
    ),
    # --- 生活 life (sc_035–sc_040) ---
    ScenarioRecord(
        id="sc_035",
        title="网购退款被客服绕",
        category="life",
        difficulty=3,
        tags=("维权", "沟通"),
        background="买到有问题的商品申请退款，客服用话术一直拖。",
        real_user_certified=False,
        persona_title="滴水不漏的客服",
        opening_line="这个情况需要您先提供更多凭证，请理解一下。",
    ),
    ScenarioRecord(
        id="sc_036",
        title="退租被克扣押金",
        category="life",
        difficulty=4,
        tags=("租房", "维权"),
        background="退租时房东以墙面有划痕为由要扣大半押金。",
        real_user_certified=False,
        persona_title="锱铢必较的房东",
        opening_line="这墙弄成这样，押金肯定得扣，你自己看。",
    ),
    ScenarioRecord(
        id="sc_037",
        title="二手买家恶意压价",
        category="life",
        difficulty=2,
        tags=("二手", "议价"),
        background="你在二手平台卖东西，买家收货后挑刺要退一半钱。",
        real_user_certified=False,
        persona_title="得寸进尺的买家",
        opening_line="东西和描述不太一样，退我一半我就不退货。",
    ),
    ScenarioRecord(
        id="sc_038",
        title="朋友借钱迟迟不还",
        category="life",
        difficulty=3,
        tags=("金钱", "朋友"),
        background="半年前借给朋友一笔钱，对方一直不提还钱的事。",
        real_user_certified=False,
        persona_title="装糊涂的朋友",
        opening_line="最近啊？手头还是有点紧，过阵子的哈。",
    ),
    ScenarioRecord(
        id="sc_039",
        title="冷战后先开口",
        category="life",
        difficulty=3,
        tags=("感情", "沟通"),
        background="和对象因小事冷战三天，你想先服软又不想全认错。",
        real_user_certified=False,
        persona_title="还在赌气的对象",
        opening_line="（对方没看你）怎么，今天想起来跟我说话了？",
    ),
    ScenarioRecord(
        id="sc_040",
        title="拒绝亲戚的人情请托",
        category="life",
        difficulty=3,
        tags=("家人", "拒绝"),
        background="亲戚想让你帮忙办超出你能力范围的事。",
        real_user_certified=False,
        persona_title="不好拒绝的长辈亲戚",
        opening_line="你在大城市认识人多，这点小忙不算什么吧？",
    ),
)


_BY_ID: dict[str, ScenarioRecord] = {record.id: record for record in SCENARIO_CATALOG}

# In-process registry for user-created scenarios (POST /v1/scenarios/
# custom). v1 keeps these in memory — single uvicorn worker, and the
# `*_repo_backend` knobs all default to `memory`. Durable storage is a
# follow-up (see the `app.services.sessions.scenario_seed` docstring).
_CUSTOM: dict[str, ScenarioRecord] = {}


def register_custom_scenario(record: ScenarioRecord) -> None:
    """Make a user-created scenario resolvable by `get_record_by_id`, so
    `POST /v1/sessions` can immediately practise against its id."""
    _CUSTOM[record.id] = record


def get_record_by_id(scenario_id: str) -> ScenarioRecord | _FallbackRecord:
    """Return the row for `scenario_id` — a registered custom scenario,
    a static catalog entry, or the fallback (so unknown ids don't break
    session create; sprint-1 wants "any string in works" for demos)."""
    custom = _CUSTOM.get(scenario_id)
    if custom is not None:
        return custom
    return _BY_ID.get(scenario_id, FALLBACK_RECORD)


__all__ = [
    "FALLBACK_RECORD",
    "SCENARIO_CATALOG",
    "ScenarioRecord",
    "get_record_by_id",
    "register_custom_scenario",
]
