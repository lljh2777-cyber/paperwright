# Stage B 自生成 PDF 视觉检查

检查对象由 `tests/pdf_fixture_factory.py` 临时生成，未提交 PDF/PNG。

| 对象 | SHA-256 | 人工观察 |
|---|---|---|
| 第 1 页 110 DPI render | `838c9be17d374ab53ccefc9ccbeb536e5207cd85a56b2e3ef3c5f3cfe6dabfae` | 标题、正文、2×2 表格、右侧红蓝渐变图与说明均可见 |
| 第 2 页 110 DPI render | `fc34e07a3991a963d0b4cb3b5d20c2e2b0767e360948b2836b5c5ce53a14e21c` | 跨栏标题与左右两栏各两行均可见，无视觉交叠 |
| 提取 PNG | `5fdc1b2a9a0760f8ca8e528710d231ee64575dab1836ed6ee15cefa089844428` | 16×12 RGB 渐变对象；非空白、非整页截图、与第 1 页图像内容一致 |

复现命令：

```bash
PYTHONPATH=src:tests python -c \
  'from pathlib import Path; from pdf_fixture_factory import create_born_digital_fixture; create_born_digital_fixture(Path("/tmp/paper2md-stageb.pdf"))'
pdftoppm -png -r 110 /tmp/paper2md-stageb.pdf /tmp/paper2md-stageb-page
PYTHONPATH=src python -m paper2md convert \
  /tmp/paper2md-stageb.pdf /tmp/paper2md-stageb-output
```

视觉观察属于人工证据；文件结构、尺寸、颜色数和哈希另由自动测试验证。
