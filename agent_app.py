"""
多Agent协同运营自动化系统 
运行方式：
    pip install fastapi uvicorn aiosqlite aiohttp
    python this_file.py
然后浏览器打开 http://localhost:8000
"""

import asyncio
import sqlite3
import random
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import aiohttp

# ==================== 配置 ====================
DB_NAME = "ops.db"
# 飞书 Webhook 地址（换成自己的真实地址才能收到通知）
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"

# ==================== 数据库 ====================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        status TEXT,
        result TEXT,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()

def log_task(task_type: str, status: str, result: str = ""):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO tasks (type, status, result, created_at) VALUES (?, ?, ?, ?)",
              (task_type, status, result, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return c.lastrowid

def update_task(task_id: int, status: str, result: str = ""):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE tasks SET status = ?, result = ? WHERE id = ?", (status, result, task_id))
    conn.commit()
    conn.close()

def get_recent_tasks(limit=20):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "type": r[1], "status": r[2], "result": r[3], "created_at": r[4]} for r in rows]

# ==================== 飞书通知 ====================
async def send_feishu_card(strategies: list, data: dict):
    if "xxxx" in FEISHU_WEBHOOK:
        print("[飞书] 未配置真实 Webhook，跳过发送。策略：", strategies)
        return

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "运营自动化执行通知"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**当前数据**\n订单：{data.get('orders')}，转化率：{data.get('conversion')}%"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": "**执行策略**\n" + "\n".join(f"- {s}" for s in strategies)}}
            ]
        }
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(FEISHU_WEBHOOK, json=card, timeout=5)
    except Exception as e:
        print(f"[飞书] 发送失败: {e}")

# ==================== Agent 定义 ====================
class AgentBase:
    def __init__(self, name):
        self.name = name
        self.queue = asyncio.Queue()
        self.running = False

    async def send(self, target: 'AgentBase', task: dict):
        await target.queue.put(task)

    async def receive(self):
        return await self.queue.get()

class DataCollectorAgent(AgentBase):
    def set_pipeline(self, analyzer):
        self.analyzer = analyzer

    async def run(self):
        self.running = True
        while self.running:
            data = {
                "orders": random.randint(70, 150),
                "visitors": random.randint(500, 1200),
                "conversion": round(random.uniform(2.0, 5.5), 2),
                "tickets": random.randint(5, 30),
            }
            task = {"id": datetime.now().timestamp(), "type": "RAW_DATA", "payload": data}
            print(f"[{self.name}] 采集数据: {data}")
            await self.send(self.analyzer, task)
            await asyncio.sleep(10)

class AnalyzerAgent(AgentBase):
    def set_pipeline(self, decision_maker):
        self.decision_maker = decision_maker

    async def run(self):
        self.running = True
        while self.running:
            task = await self.receive()
            data = task["payload"]
            issues = []
            if data["orders"] < 90:
                issues.append("订单量下降")
            if data["conversion"] < 3.0:
                issues.append("转化率偏低")
            if data["tickets"] > 20:
                issues.append("客诉激增")
            if issues:
                alert = {"id": task["id"], "type": "ALERT", "payload": {"issues": issues, "data": data}}
                print(f"[{self.name}] 发现问题: {issues}")
                await self.send(self.decision_maker, alert)
            else:
                print(f"[{self.name}] 指标正常")

class DecisionMakerAgent(AgentBase):
    def set_pipeline(self, executor):
        self.executor = executor

    async def run(self):
        self.running = True
        while self.running:
            task = await self.receive()
            issues = task["payload"]["issues"]
            strategies = []
            for issue in issues:
                if "订单" in issue:
                    strategies.append("推送限时优惠券")
                if "转化" in issue:
                    strategies.append("A/B测试落地页")
                if "客诉" in issue:
                    strategies.append("升级客服并发送安抚券")
            action = {"id": task["id"], "type": "ACTION", "payload": {"strategies": strategies, "data": task["payload"]["data"]}}
            print(f"[{self.name}] 决策: {strategies}")
            # 写入数据库
            if hasattr(self, 'on_action'):
                await self.on_action(action)
            await self.send(self.executor, action)

class ExecutorAgent(AgentBase):
    async def run(self):
        self.running = True
        while self.running:
            task = await self.receive()
            strategies = task["payload"]["strategies"]
            for s in strategies:
                print(f"[{self.name}] 执行: {s}")
                await asyncio.sleep(1)
            print(f"[{self.name}] 所有动作完成")
            if hasattr(self, 'on_complete'):
                await self.on_complete(task)

# ==================== FastAPI 应用 ====================
app = FastAPI()

# 初始化 Agent
collector = DataCollectorAgent("采集Agent")
analyzer = AnalyzerAgent("分析Agent")
decision = DecisionMakerAgent("决策Agent")
executor = ExecutorAgent("执行Agent")

collector.set_pipeline(analyzer)
analyzer.set_pipeline(decision)
decision.set_pipeline(executor)

# 回调：记录任务 + 飞书通知
async def on_action(action):
    tid = log_task("ACTION", "running", str(action["payload"]["strategies"]))
    action["db_id"] = tid
decision.on_action = on_action

async def on_complete(task):
    if "db_id" in task:
        update_task(task["db_id"], "completed", str(task["payload"]["strategies"]))
    await send_feishu_card(task["payload"]["strategies"], task["payload"]["data"])
executor.on_complete = on_complete

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(collector.run())
    asyncio.create_task(analyzer.run())
    asyncio.create_task(decision.run())
    asyncio.create_task(executor.run())
    print("多Agent系统已启动")

# 前端页面（内嵌 HTML）
FRONTEND_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>多Agent运营自动化</title>
    <style>
        body { font-family: sans-serif; margin: 40px; }
        button { padding: 10px 20px; font-size: 16px; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
        th { background: #f0f0f0; }
    </style>
</head>
<body>
    <h1>多Agent协同运营自动化系统</h1>
    <button onclick="manualTrigger()">手动触发一次工作流</button>
    <button onclick="refreshTasks()">刷新任务日志</button>
    <div id="status"></div>
    <h2>最近任务记录</h2>
    <table>
        <thead><tr><th>ID</th><th>类型</th><th>状态</th><th>结果</th><th>时间</th></tr></thead>
        <tbody id="task-list"></tbody>
    </table>

    <script>
        async function manualTrigger() {
            const res = await fetch('/api/trigger', {method:'POST'});
            const data = await res.json();
            document.getElementById('status').innerText = '已触发，数据：' + JSON.stringify(data.data);
            setTimeout(refreshTasks, 2000);
        }
        async function refreshTasks() {
            const res = await fetch('/api/tasks');
            const tasks = await res.json();
            const tbody = document.getElementById('task-list');
            tbody.innerHTML = tasks.map(t => `<tr>
                <td>${t.id}</td><td>${t.type}</td><td>${t.status}</td>
                <td>${t.result || ''}</td><td>${t.created_at}</td>
            </tr>`).join('');
        }
        refreshTasks();
        setInterval(refreshTasks, 8000);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return FRONTEND_HTML

@app.get("/api/tasks")
async def api_get_tasks():
    return get_recent_tasks(20)

@app.post("/api/trigger")
async def api_manual_trigger():
    data = {
        "orders": random.randint(70, 150),
        "visitors": random.randint(500, 1200),
        "conversion": round(random.uniform(2.0, 5.5), 2),
        "tickets": random.randint(5, 30),
    }
    task = {"id": datetime.now().timestamp(), "type": "RAW_DATA", "payload": data}
    await collector.queue.put(task)
    return {"status": "triggered", "data": data}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)