# Cube H2H Win Rate Calculator

魔方选手 H2H（Head-to-Head）对战胜率计算器。输入两位选手的 WCA ID，通过非官方 WCA REST API 获取历史成绩，通过蒙特卡洛模拟估算对战胜率，结果以网页形式展示。

## 使用方法

```
python3 cube_h2h.py
```

然后打开浏览器访问 http://localhost:8080

## 功能

1. **输入选手**: 在输入框中直接输入 WCA ID（如 `2009ZEMD01`）
2. **选择项目**: 支持 `333` / `222` / `444` / `555` / `333oh` / `333bf`
3. **选择类型**: Single（单次）或 Average（平均）
4. **点击计算**: 从非官方 API 并行拉取两人数据，每人只需一次请求（成绩已内嵌在选手数据中），用蒙特卡洛模拟 50,000 次估算胜率

## 胜率计算原理

- 从每位选手的历史成绩中提取指定项目的成绩（排除 DNF/DNS）
- 计算均值和标准差，建立正态分布模型
- 蒙特卡洛模拟 50,000 次，每次从两个选手的分布中各采样一次比较
- 输出双方胜率 + 统计数据（样本数、最佳、最差、均值、标准差）

## 界面

深色主题页面，包含可视化胜率条形图和双方详细统计对比。

## 依赖

- Python 3.8+
- [requests](https://pypi.org/project/requests/)

```bash
pip install requests
```

## 数据来源

所有成绩数据来自 [非官方 WCA REST API](https://github.com/robiningelbrecht/wca-rest-api)（Robin Ingelbrecht），底层数据源自 [World Cube Association](https://www.worldcubeassociation.org/) 公开成绩导出。
