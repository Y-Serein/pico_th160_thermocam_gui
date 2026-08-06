# TN160 Thermal Workbench — Windows使用与打包

## 直接使用

正式交付物是：

```text
dist/TN160_Thermal_Workbench.zip
```

使用步骤：

1. 将ZIP完整解压到普通本地目录。
2. 不要从压缩软件预览窗口直接运行，也不要只复制其中的EXE。
3. 双击`TN160_Thermal_Workbench/TN160_Thermal_Workbench.exe`。
4. 接入设备后，在Monitor页选择COM口并开始采集。

## 热状态原始帧采集

“热状态采集”页复用固件已有的`0xAA / RAW_U16`模式，不需要更换固件或修改串口协议。

1. 先停止“实时诊断”，确保串口没有被其他程序占用。
2. 填写设备标签，选择“冷启动/热机RST”和`T0～T3`热状态，再填写场景标签，例如`room`、`bb50`。
3. 保持目标、距离和视场不变，点击“采集并保存”。
4. 工具读取切换前热状态、采集32张原始帧，然后自动恢复`0x11`监控模式。
5. 结果保存在应用目录的`thermal_state_capture/`，每次采集包含NPZ原始数组和带SHA256的manifest。

同一台设备的低温/高温参考必须使用不同场景标签；冷启动与热机RST也必须选对路径，不能混合。不要在一次32帧采集中移动目标。在2 Mbaud下32帧线速时间下限约6.2秒，加模式切换和前后遥测通常需7～8秒。该页用于研发判定全局gain/offset模型还是逐像素NUC，不会写设备Flash，也不应成为每台量产设备的默认工位步骤。

## 6分钟Flash V2自动标定

每台设备的工位操作固定为一次：

1. 设备完全冷却后，让镜头正对固定高发射率平场板或平场罩；不需要连续占用精密黑体。
2. 上电后立即进入“热状态采集”页，确认串口和设备标签。
3. 点击“6分钟自动标定并写入Flash V2”，此后保持设备、平场和距离不动。
4. 工具自动在约10、60、120、300秒的有效FFC后采集32帧，计算每机四状态残差，写入Flash并做CRC回读。
5. 只有界面显示“Flash写入、CRC回读通过”且总耗时不超过360秒，才能进入冷启动与热机RST温漂验收。

标定中断或写入失败时，固件保留原V1标定并回退，不按成功件放行。完整原始帧、FFC map、factory gain、V2残差、CRC和manifest保存在应用目录的`thermal_v2_calibration/`。平场解决空间一致性与温漂建模；绝对温准仍需后续用精密黑体做短时资格验证，不能用未知房间场景替代。

应用使用PyInstaller `onedir`结构。EXE旁边的`_internal/`包含Python、Qt、NumPy和Matplotlib运行库，缺少其中任何文件都无法启动。

支持Windows 10/11 x64。首次运行无数字签名的软件时，Windows可能显示SmartScreen提示；确认文件来自本项目并核对SHA256后，可通过“更多信息”继续运行。设备通信还需要正确安装CH346/QinHeng USB串口驱动。

## 重新打包

双击：

```text
build_win.bat
```

脚本会：

1. 复用或创建`.venv_win/`本地构建环境。
2. 把已有`logs/`、`cali_data_backup/`、`thermal_state_capture/`、`ffc_runtime_diagnostic/`和`thermal_v2_calibration/`合并保存到`dist/`之外的`.workbench_user_data/`。
3. 删除旧的`build/`和`dist/`。
4. 使用`thermocam_gui.spec`构建完整应用目录。
5. 在不夹带本机日志或标定数据的情况下，生成唯一可发送ZIP和SHA256校验文件。
6. 把本机用户数据恢复到新的本地运行目录，再删除`build/`缓存。

最终目录固定为：

```text
dist/
├── TN160_Thermal_Workbench/
│   ├── TN160_Thermal_Workbench.exe
│   ├── _internal/
│   └── README_WIN.md
├── TN160_Thermal_Workbench.zip
└── TN160_Thermal_Workbench.zip.sha256.txt
```

`dist/TN160_Thermal_Workbench/`用于本机直接运行，ZIP用于发送给其他电脑。二者是同一版本，不是两套候选版本。

`.venv_win/`是隐藏的本地构建依赖缓存，不是交付物；需要释放空间时可以删除，下次构建会自动重建。`thermocam_gui.spec`是正式打包配置，必须保留。

`.workbench_user_data/`是本机日志、标定备份和RAW采集的持久保护副本，不会放进对外ZIP，也不会纳入Git。除非这些数据已另行归档，不要手动删除该目录。

## 源码目录

```text
app.py                 # 唯一GUI启动入口
thermocam_gui/         # 正式应用模块
├── main_window.py     # 主窗口和四页导航
├── flash_tab.py       # UF2烧录
├── monitor_tab.py     # 实时热图、趋势和日志导出
├── device_calibration_tab.py # 运行0°C/50°C设备标定
├── calibration_data_tab.py   # 只读查看Flash标定数据
├── thermal_capture_tab.py    # T0～T3 RAW_U16原始帧采集
├── thermal_capture_io.py     # 原始帧栈、manifest和SHA256保存
├── protocol.py        # 当前19255字节串口协议
├── workers.py         # 后台串口线程
├── port_utils.py      # 串口扫描与探测
└── ui_style.py        # 统一界面样式
scripts/
└── sniff_serial.py    # Linux串口帧诊断工具，不参与GUI打包
```

`scripts/sniff_serial.py`仅在排查CH346串口帧同步时使用。旧的`src/view_temp.py`仍按19221字节历史协议解析，已经删除，避免误用于当前54字节telemetry固件。
