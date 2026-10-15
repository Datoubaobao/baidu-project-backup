# 学生三步上手

用本地 Codex 安装并体验“百度网盘项目备份与恢复”技能。第一次先使用无敏感数据的小测试项目，保留本地文件。

## 第一步：把这段话发给 Codex

打开本地 Codex，新建或进入一个对话，复制发送：

```text
使用 $skill-installer 安装 https://github.com/Datoubaobao/baidu-project-backup 的 v0.1.0 版本。技能位于仓库根目录（path: .），安装名称为 baidu-project-backup。只安装，不执行备份或清理。
```

其中 `$skill-installer` 是 Codex 的内置技能安装器，GitHub 链接是公开源码仓库；`path: .` 表示技能就在仓库根目录。固定版本便于全班使用同一份发布代码。

无需先手动下载 ZIP，也不用把自己的项目上传到 GitHub。若当前环境提示需要网络或文件写入权限，核对目标是这个仓库及技能安装目录，再按实际提示处理。

## 第二步：等安装完成，再发一条消息确认

```text
请确认你能找到 baidu-project-backup 技能，并简要说明它的用途。现在不要执行备份或清理。
```

如果 Codex 能找到并说明技能，安装检查就完成了。Codex 会自动发现新技能；没有识别到时，重启 Codex 后再试。安装器的使用和发现机制见 [OpenAI 官方文档](https://learn.chatgpt.com/docs/build-skills#install-curated-skills-for-local-use)。

## 第三步：在小测试项目中体验备份

打开一个你愿意备份的小项目，保存所有编辑，然后说：

```text
使用 $baidu-project-backup 将当前项目备份到我的百度网盘，保留本地文件。
```

需要自己的百度网盘账号和官方客户端；自行在官方界面登录，不向助手提供密码或 Cookie。完整客户端流程面向 macOS；自动操作需要环境提供桌面控制能力，否则由你手动上传和回下载，助手继续校验。脚本需要 Python 3.10+。

备份完成应有精确的网盘目录、真实回下载校验结果，以及项目中的恢复说明。仅有“本地打包完成”或“上传 100%”都还不算完成全流程。

熟悉后，如确实要归档本地项目，可另外发送：

```text
使用 $baidu-project-backup 备份并清理当前项目。
```

只有这个明确指令才允许在全部校验通过后移入废纸篓。技能不自动清空废纸篓，移动本身不释放磁盘空间。

## 遇到问题时

| 情况 | 可以怎么告诉 Codex |
| --- | --- |
| 找不到 `$skill-installer` | “请根据仓库 README 的手动安装步骤，将技能安装到当前环境支持的个人技能目录，检查后告诉我结果。” |
| 安装器不清楚仓库路径 | “仓库是 Datoubaobao/baidu-project-backup，版本 v0.1.0，path 为 .，name 为 baidu-project-backup。” |
| 提示同名技能已存在 | 先检查现有版本，不覆盖或重复安装；把实际提示发给老师。 |
| GitHub 下载失败 | 检查当前网络可访问该仓库，稍后重试或按 README 手动安装；不要提供账号令牌或擅自改系统网络设置。 |
| 没有客户端或桌面控制能力 | 先保留项目，按 [使用手册](USER_GUIDE.md#8-没有桌面控制工具时手动传输) 完成手动上传和真实回下载。 |

## 老师可直接转发给学生的通知

同学们，请在本地 Codex 中发送下面这段话，安装本次练习使用的项目备份技能：

```text
使用 $skill-installer 安装 https://github.com/Datoubaobao/baidu-project-backup 的 v0.1.0 版本。技能位于仓库根目录（path: .），安装名称为 baidu-project-backup。只安装，不执行备份或清理。
```

等安装完成，再发一条消息：“请确认你能找到 baidu-project-backup 技能，现在不要执行备份或清理。”未识别时先重启 Codex。

第一次请用无敏感数据的小项目，只做“备份，保留本地文件”。完整客户端流程面向 macOS，需要自己的百度网盘账号；没有桌面控制工具时可手动传输。不要提交真实备份、账号信息或密钥给老师或公开仓库。

项目主页：[baidu-project-backup](https://github.com/Datoubaobao/baidu-project-backup) · [完整使用手册](USER_GUIDE.md)
