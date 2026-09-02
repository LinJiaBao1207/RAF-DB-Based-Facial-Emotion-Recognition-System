# 模型训练

本目录包含基于 [RAF-DB](http://www.whdeng.cn/RAF/model1.html) 的人脸情绪识别模型训练 Notebook，与仓库根目录的推理服务（`backend/`）配套使用。

完整链路：**数据准备 → 训练 → 导出权重 → 放入 `models/` → 启动后端推理**。

> **说明**：本仓库不提供预训练权重。克隆后可直接预览 UI 与登录（见根目录 [README](../README.md#使用方式一览)）；**图片 / 视频情绪识别**须完成本目录下的训练流程。

---

## 目录结构

```text
training/
├── README.md
├── requirements.txt
└── notebooks/
    ├── RAF_CNN.ipynb    # CNN 基础模型
    ├── RAF_VGG.ipynb    # VGG16 迁移学习
    └── RAF_SE.ipynb     # SE 注意力 + EfficientNet（含 SavedModel 导出）
```

---

## 环境准备

**Python 3.8**（与推理端一致，为本地验证过的版本）

```cmd
cd training
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m ipykernel install --user --name=raf-training
jupyter notebook
```

> **版本提示**：训练与推理均建议使用 **Python 3.8** 与 **TensorFlow 2.10.x**，避免 SavedModel / `.h5` 兼容问题。推理端依赖见 [backend/requirements.txt](../backend/requirements.txt)。

---

## RAF-DB 数据集

RAF-DB **不能**随本仓库分发，需自行向官方申请或下载，并按以下结构放置（相对于 `training/notebooks/` 运行目录）：

```text
RAF-DB/
├── train/          # 训练集，按类别分子目录
│   ├── anger/
│   ├── disgust/
│   ├── fear/
│   ├── happy/
│   ├── normal/
│   ├── sad/
│   └── surprised/
└── test/           # 验证 / 测试集，同上 7 类
```

Notebook 内默认路径为：

- 训练集：`../RAF-DB/train`
- 验证集：`../RAF-DB/test`

若你的目录不同，请在 Notebook 开头修改 `train_dir` / `val_dir`。

**7 类情绪标签**（与推理 API 一致）：

`anger`, `disgust`, `fear`, `happy`, `normal`, `sad`, `surprised`

---

## 训练说明

| Notebook | 模型 | 输入尺寸 | 训练产物（Notebook 内默认路径） |
|----------|------|----------|--------------------------------|
| `RAF_CNN.ipynb` | 自定义 CNN | 96×96 RGB | `models/fer2013_best_model.h5` |
| `RAF_VGG.ipynb` | VGG16 迁移学习 | 96×96 RGB | `models/fer2013_best_model.h5` |
| `RAF_SE.ipynb` | SE + EfficientNet | 96×96 RGB | `models/fer2013_best.weights.h5` + `models/fer2013_saved_model/` |

各 Notebook 默认最多 **100 epoch**，含 EarlyStopping、ReduceLROnPlateau、ModelCheckpoint；具体超参见 Notebook 内代码。

> Notebook 中部分变量名沿用早期 FER2013 命名（如 `fer2013_best_model.h5`），实际使用的是 RAF-DB 数据，不影响训练逻辑。

---

## 导出到推理目录

训练完成后，将最佳 checkpoint **复制并重命名** 到仓库根目录 `models/`，文件名须与 `backend/src/config/settings.py` 中的 `MODEL_CONFIG` 一致：

| 训练产物 | 复制到（仓库根 `models/`） | 推理键名 |
|----------|---------------------------|----------|
| CNN 的 `fer2013_best_model.h5` | `RAF_CNN_83_best_model.h5` | `cnn` |
| VGG 的 `fer2013_best_model.h5` | `RAF_VGG_80_best_model.h5` | `vgg` |
| SE 的 `fer2013_saved_model/` 目录 | `RAF_SE_81_saved_model/` | `se81` |
| SE 的 `fer2013_saved_model/` 目录（另一轮最佳） | `RAF_SE_83_saved_model/` | `se83` |

**Windows 示例**（在仓库根目录执行，路径按实际修改）：

```cmd
copy training\notebooks\models\fer2013_best_model.h5 models\RAF_CNN_83_best_model.h5
copy training\notebooks\models\fer2013_best_model.h5 models\RAF_VGG_80_best_model.h5
xcopy /E /I training\notebooks\models\fer2013_saved_model models\RAF_SE_83_saved_model
```

SE 若需同时提供 `se81` 与 `se83` 两个版本，可分别保留验证准确率约 81% 与 83% 的两轮训练结果，或复制同一 SavedModel 到两个目录做演示（精度以实际 checkpoint 为准）。

导出完成后启动后端验证：

```cmd
cd backend
python main.py
```

访问 `http://localhost:5000/api/models` 确认模型已加载。

---

## 参考指标

以下为 `backend/src/config/settings.py` 中记录的验证集参考准确率（以你本地训练结果为准）：

| 模型 | 参考准确率 |
|------|-----------|
| CNN | 83.77% |
| VGG | 80.00% |
| SE-81 | 81.00% |
| SE-83 | 83.00% |

---

## 许可

训练代码与主项目相同，采用 [CC BY-NC 4.0](../LICENSE)。RAF-DB 数据集的使用须遵守其官方许可条款。
