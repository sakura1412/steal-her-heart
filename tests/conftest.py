import pytest

from steal_her_heart.project_initialization import QUESTIONS, ProjectStore

PROJECT_ANSWERS = {
    "product": "自主研发的硬件产品，解决特定使用问题",
    "industry": "消费电子／专业硬件",
    "target_customers": "国内制造企业的采购与使用人员",
    "market_scope": "中国大陆",
    "strengths": "可维修、服务响应及时（用户自述）",
    "weaknesses": "待确认",
    "price_range": "人民币1万至3万元/台（测试自述）",
    "objective": "支持路线图与定价决策",
    "readers_and_priorities": (
        "产品经理 | 差距与路线图 | 功能矩阵；投资人（投资委员会） | 市场机会与风险 | 投资备忘录"
    ),
    "known_competitors": "",
    "special_notes": "避开未署名转载；保留适用限制；中文输出（测试配置）",
}


@pytest.fixture
def project_store(tmp_path):
    store = ProjectStore(tmp_path / "project.sqlite3")
    store.start()
    for field in QUESTIONS:
        store.answer(field, PROJECT_ANSWERS[field])
    return store


@pytest.fixture
def initialized_project(project_store):
    return project_store.load().context


@pytest.fixture
def project_args(project_store):
    return ["--project", str(project_store.path)]
