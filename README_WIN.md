# TN160 Toolbox — Windows 打包

## 一次性准备
1. 装 **Python 3.11+**：https://www.python.org/downloads/  
   安装时勾上 **Add python.exe to PATH**。
2. 装 TN160 的 USB 串口驱动（CH346 / QinHeng）。接上设备后，设备管理器里应能看到两个 COM 端口。

## 打包 EXE
1. 在资源管理器里打开 `C:\Serein_Y\Sipeed\pico_tn160\tools\thermocam_gui\`。
2. 双击 **`build_win.bat`**。
   - 第一次会拉依赖，大概 5–10 分钟 + 几百 MB 下载。
   - 之后再跑只会重新打包，1–2 分钟。
3. 完成后生成 **`dist\thermocam_gui.exe`**（约 80–120 MB，onefile）。

## 使用
- 双击 `thermocam_gui.exe` 启动。
- **Monitor** 标签：下拉选 COM 口 → 默认 2 Mbaud → `Start`。
- **Calibration Dump** 标签：选同一个 COM 口 → `Trigger Dump`。

## 目录产物
```
thermocam_gui/
├── build_win.bat           ← 双击打包
├── requirements_win.txt
├── .venv_win/              ← 本地 venv（build_win.bat 自动建，可随意删）
├── build/ dist/            ← PyInstaller 中间/输出
└── dist/thermocam_gui.exe  ← 最终产物
```

`.venv_win/`、`build/`、`dist/` 都可以随时整个删掉重建。
