# 百度网盘项目备份与恢复

一个面向 Codex 的开源技能：将当前项目完整备份到自己的百度网盘，实际回下载校验后，按用户指令清理本地，并留下恢复入口。

[用户使用手册](docs/USER_GUIDE.md) · [技能指令](SKILL.md) · [客户端操作参考](references/baidu-client.md) · [MIT 许可证](LICENSE)

## 能做什么

- 完整归档当前项目，包括隐藏文件、Git 历史、素材、产物和依赖，不按 `.gitignore` 排除。
- 默认生成 2 GiB 分卷，记录 SHA-256、权限、修改时间、扩展属性及符号链接。
- 要求真实上传、真实回下载、全部校验通过，才允许按指令清理。
- 清理时移入废纸篓，在原路径保留 `AGENTS.md`、JSON 标记和中文恢复说明。
- 支持完整恢复，或只下载指定文件涉及的分卷进行恢复。

“备份”保留源文件；“备份并清理”才会移入废纸篓。**本技能不自动清空废纸篓，移入废纸篓本身不释放磁盘空间。**

## 使用前准备

- 本地 Codex 环境，能够读写项目文件、运行 Python 3.10+；脚本只使用 Python 标准库。
- 用户自己的百度网盘账户和官方客户端，或已有可信的上传/下载连接器。
- 自动操作客户端需要环境提供桌面控制能力；没有该能力时，可以手动完成上传和回下载。
- 足够的暂存和回下载空间：通常至少需要项目体积约两倍的额外空间及余量，也可使用外置盘。

完整客户端流程面向 macOS。Linux 可运行本地辅助脚本，但桌面流程未验证；Windows 不在当前支持范围。此项目不包含百度网盘 API 接入、登录凭据或桌面控制工具。

## 安装

在 Codex 中使用内置安装器：

```text
使用 $skill-installer 安装 https://github.com/Datoubaobao/baidu-project-backup 中的技能，技能位于仓库根目录，名称为 baidu-project-backup。
```

也可以手动安装到个人技能目录，已有同名目录时先检查内容，避免覆盖：

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/Datoubaobao/baidu-project-backup.git \
  "$HOME/.agents/skills/baidu-project-backup"
```

Codex 的个人技能目录与自动发现行为见 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。安装后若没有发现技能，重启 Codex；同名旧版本只保留一份，避免重复加载。

## 开始使用

在需要备份的项目中对 Codex 说：

```text
使用 $baidu-project-backup 将当前项目备份到我的百度网盘，保留本地文件。
```

需要归档并清理时：

```text
使用 $baidu-project-backup 备份并清理当前项目。
```

以后在原项目中说：

```text
使用 $baidu-project-backup 恢复这个项目，并继续在原路径工作。
```

或只恢复一个文件：

```text
使用 $baidu-project-backup 恢复项目中的 output/report.pdf，放到新的恢复目录。
```

安装、手动传输、命令参数、恢复索引和常见问题请阅读 [用户使用手册](docs/USER_GUIDE.md)。

## 隐私与边界

**备份分卷不加密，也不自动脱敏。** `.env`、`.git`、配置文件、扩展属性以及文件名都可能包含私密信息；索引和备份信息也会记录本机项目路径。只上传到自己的私有网盘。公开的是技能源码，真实备份、清单、恢复标记和传输证据应保持私密。

符号链接保留链接本身，不打包目录外目标。Git linked worktree 的根 `.git` 指针会被拒绝。特殊设备、套接字、FIFO 会中止归档；硬链接按独立文件保存。ACL、文件所有者、跨平台文件系统语义和 Codex 服务端聊天历史不在还原承诺范围内。

脚本校验文件内容，无法独立证明文件来自云端；真实远端来源依赖客户端或连接器的上传、回下载操作。不要用本地复制代替真实回下载。

## 开发与贡献

```bash
python3 -B scripts/test_backup.py
```

测试仅使用临时合成数据，不访问真实网盘。提交问题或改进前请阅读 [贡献说明](CONTRIBUTING.md) 和 [安全说明](SECURITY.md)。

本项目独立开发，与百度及 OpenAI 无隶属关系。使用 MIT 许可证。
