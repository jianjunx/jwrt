# Kwrt 精简固件方案 —— 移除 NAS 功能与 iStore 应用商店

> 基于 <https://github.com/kiddin9/Kwrt>（2026-10-21 拉取）及其配套 feed
> <https://github.com/kiddin9/op-packages> 的完整分析。
> 所有结论均以仓库源码、构建产物（manifest / ImageBuilder 元数据 / rootfs opkg 状态）交叉验证。

---

## 目录

1. [结论摘要](#1-结论摘要)
2. [构建系统与目录结构](#2-构建系统与目录结构)
3. [镜像包集合的形成机制（关键）](#3-镜像包集合的形成机制关键)
4. [NAS 相关组件清单与引用位置](#4-nas-相关组件清单与引用位置)
5. [iStore 相关组件清单与引用位置](#5-istore-相关组件清单与引用位置)
6. [移除方案：改动点清单](#6-移除方案改动点清单)
7. [移除后仍需保留的依赖](#7-移除后仍需保留的依赖)
8. [风险与副作用评估](#8-风险与副作用评估)
9. [最终执行步骤清单](#9-最终执行步骤清单)
10. [附录：构建后验证方法](#10-附录构建后验证方法)

---

## 1. 结论摘要

| 项目 | 结论 |
| --- | --- |
| 构建基线 | 官方 OpenWrt `openwrt-25.12` 分支 + kiddin9 补丁/脚本 + `kiddin9/op-packages` 自定义 feed（893 个源码包、443 个 luci-app） |
| NAS 在镜像中的形态 | `luci-app-samba4`、`luci-app-diskman`、`automount`、`luci-app-partexp` 等被**显式装入**，`samba4-server`、`mdadm`、`smartd/smartmontools`、`mount-utils`、`ntfs3-mount`、`parted/lsblk/blkid` 等由**依赖自动拉入** |
| iStore 在镜像中的形态 | `luci-app-istorex` 被**显式装入**，`luci-app-store`、`quickstart`、`luci-app-quickstart`、`taskd`、`luci-lib-taskd`、`luci-app-ttyd` 由**依赖自动拉入**（istorex → quickstart + store + taskd 的全家桶链） |
| 与 .config 的关系 | 配置种子与最终 `.config` 里这些包均为 `未选中/m`；镜像内容实际由「显式安装清单 + opkg 依赖闭包」决定，**仅改 .config 不足以保证移除**，必须做多层防御（见 §6） |
| 核心改动量 | 2 个配置种子 + 1 个补丁 +（可选）fork feed 删包；保留 `block-mount`、`e2fsprogs`、`mkf2fs`、`partx-utils`、`openssh-sftp-server` 等基础件即可不破坏其他功能 |

---

## 2. 构建系统与目录结构

### 2.1 仓库结构

```
Kwrt/
├── .github/workflows/
│   ├── Openwrt-AutoBuild.yml     # 主构建流水线（由 repository_dispatch 触发，target 由矩阵传入）
│   └── repo-dispatcher.yml       # 手动/批量触发入口（一次派发 x86_64、rockchip_armv8 等多 target）
├── devices/
│   ├── common/
│   │   ├── .config               # 公共配置种子（约 119 行，只写与默认值的差异）
│   │   ├── diy.sh                # 核心定制脚本：改 feeds、装 feed、注入 DEFAULT_PACKAGES、打补丁前置处理
│   │   ├── settings.ini          # REPO_URL / REPO_BRANCH / CONFIG_FILE / DIY_SH 等构建参数
│   │   ├── patches/              # 打进 OpenWrt 源码树的补丁（default-packages、imagebuilder、luci_mk…）
│   │   └── diy/                  # 直接覆盖进源码树的文件（banner、opkg 补丁等）
│   ├── x86_64/                   # 各 target 目录：.config 追加段 + diy.sh + 可选 patches/
│   └── <其他 23 个 target>/
└── （编译时克隆 openwrt/openwrt@openwrt-25.12 为工作树，再叠加上述内容）
```

### 2.2 构建流程（Openwrt-AutoBuild.yml）

1. **Load Settings.ini** —— 读取 `devices/common/settings.ini`（可被 `devices/<target>/settings.ini` 覆盖）
2. **Clone source code** —— `git clone https://github.com/openwrt/openwrt -b openwrt-25.12 openwrt`
3. **Load custom configuration** —— `cp -rf devices/common/. openwrt/` → `cp -rf devices/<target>/. openwrt/` →
   执行 `devices/common/diy.sh` → 以 `devices/common/.config` 为底、`devices/<target>/.config` 追加，生成 `.config` → 执行 `devices/<target>/diy.sh`
4. **Apply patches** —— 对 `devices/<target>/patches/*.patch`（含从 common 拷来的）逐个 `patch -p1`
5. **Defconfig** —— `make defconfig` 解析依赖闭包
6. **Compile** —— `make -j$(($(nproc)+1))`（失败自动 `make V=s` 重试）
7. **Deploy** —— imagebuilder + initramfs 上传至 `dl.openwrt.ai`；固件/manifest/meta 文件由发布侧组装产出

### 2.3 自定义 feed 与关键脚本改动

`devices/common/diy.sh`（关键行）：

```bash
# L5  增加 kiddin9 自定义 feed（NAS/iStore 包的真正所在地）
sed -i '$a src-git kiddin9 https://github.com/kiddin9/op-packages.git;main' feeds.conf.default
# L14-16  全量安装 feeds（仅建立 package/feeds/ 软链接，不写 .config）
./scripts/feeds update -a
./scripts/feeds install -a -p kiddin9 -f
./scripts/feeds install -a
# L42-44  向 include/target.mk 的 DEFAULT_PACKAGES 注入默认应用
sed -i "s/DEFAULT_PACKAGES:=/DEFAULT_PACKAGES:=luci-app-advancedplus luci-app-sysctl luci-app-firewall \
luci-app-package-manager luci-app-upnp luci-app-syscontrol \
luci-app-wizard luci-base luci-compat luci-lib-ipkg luci-lib-fs \
coremark wget-ssl curl autocore htop nano zram-swap kmod-lib-zstd kmod-tcp-bbr bash openssh-sftp-server \
block-mount resolveip ds-lite swconfig luci-app-fan luci-app-footstrap-files luci-app-wifihistory /" include/target.mk
```

其余改动：换用 immortalwrt 的 openssl/ppp、拉取 lede 的 kernel hack 补丁、禁签名、改版本号等，与本次精简无关。

---

## 3. 镜像包集合的形成机制（关键）

这是移除方案的依据，务必理解三层机制：

### 3.1 三层选择来源

| 层 | 机制 | 对应文件 |
| --- | --- | --- |
| ① 配置层 | `devices/*/.config` 种子 + `make defconfig` 依赖闭包 → `CONFIG_PACKAGE_*=y/m/n` | `devices/common/.config`、`devices/<target>/.config` |
| ② 默认包层 | `include/target.mk` 的 `DEFAULT_PACKAGES`（含 diy.sh 注入 + `DEFAULT_PACKAGES.router/nas` + target 追加）→ 经 `scripts/target-metadata.pl` 生成 `select DEFAULT_<pkg>`，defconfig 时默认置 y | `devices/common/diy.sh:42-44`、`devices/common/patches/default-packages.patch` |
| ③ 安装清单层 | rootfs 组装时按「显式安装清单」跑 opkg，**依赖由本地包索引自动补齐** | 发布链路（openwrt.ai 组装预设）/ ImageBuilder `BUILD_PACKAGES` |

### 3.2 实证（2026-10-08 x86-64 发布固件）

从 `kwrt-10.08.2026-x86-64-generic.manifest` 及 rootfs 的 `/usr/lib/opkg/status` 提取：

- 共 **470** 个包，其中 **99 个显式安装**（无 `Auto-Installed` 标志）、371 个依赖拉入（有 `Auto-Installed` 标志）。
- 显式集合 = `Default-Packages`（98 个：默认应用 + x86 硬件兼容包）+ `kernel` + **额外插件：
  `luci-app-istorex`、`luci-app-samba4`、`luci-app-diskman`、`automount`、`luci-app-passwall`、
  `luci-theme-vantage`、`luci-app-gpsysupgrade` 等**。
- 依赖拉入的典型成员：`samba4-server`、`samba4-libs`、`luci-app-store`、`quickstart`、`taskd`、
  `mdadm`、`smartd`、`smartmontools`、`mount-utils`、`ntfs3-mount`、`luci-app-partexp`、`luci-app-ttyd`。
- 而构建产物中的 `.config`（`config.buildinfo` / `x86_64.config`，两份互相一致）里
  `samba4-server`、`luci-app-store`、`luci-app-istorex`、`quickstart` 等**全部是「未选中」**。

**结论：镜像里出现 NAS/iStore 不是 .config 选出来的，而是安装清单层显式加入后被 opkg 依赖闭包放大的。**
因此精简必须「配置层黑名单 + 清掉安装清单/组装层入口 + feed 层封堵」三管齐下，见 §6。

---

## 4. NAS 相关组件清单与引用位置

### 4.1 镜像中实际存在的 NAS 组件（按角色分组）

| 角色 | 包名（rootfs 中实测） | 进入方式 | 定义位置（op-packages） |
| --- | --- | --- | --- |
| 文件共享 | `luci-app-samba4` | 显式 | `luci-app-samba4/Makefile`（`LUCI_DEPENDS:=+luci-base +samba4-server`） |
| | `samba4-server`、`samba4-libs` | 依赖 | `feeds/packages` 官方 samba4 |
| 磁盘/分区管理 | `luci-app-diskman` | 显式 | `luci-app-diskman/Makefile`（依赖 blkid、e2fsprogs、parted、smartmontools、dosfstools、kmod-fs-*、exfat-*、可选 mdadm/btrfs/lsblk） |
| | `luci-app-partexp` | 依赖（automount 拉入） | `luci-app-partexp/Makefile`（`+fdisk +block-mount +bc +blkid +parted +btrfs-progs +losetup +resize2fs +e2fsprogs +f2fs-tools +kmod-loop`） |
| 自动挂载 | `automount` | 显式 | `automount/Makefile`（`+luci-app-partexp +block-mount +e2fsprogs +kmod-usb-storage* +kmod-fs-ext4/exfat/vfat +ntfs3-mount`；自带 hotplug：`files/15-automount`、`files/11-anonmount`） |
| | `ntfs3-mount`、`mount-utils` | 依赖 | `automount/Makefile` / `quickstart` 依赖 |
| RAID/健康 | `mdadm`、`smartd`、`smartmontools`、`smartmontools-drivedb` | 依赖（quickstart/diskman 拉入） | `quickstart/Makefile`、`luci-app-diskman/Makefile` |
| 分区/文件系统工具 | `parted`、`blkid`、`fdisk`、`cfdisk`、`lsblk`、`losetup`、`resize2fs`、`btrfs-progs`、`exfat-mkfs`、`exfat-fsck`、`f2fs-tools`、`f2fsck`、`dosfstools` | 依赖 | 同上各 Makefile |
| 内核文件系统 | `kmod-fs-btrfs/exfat/ntfs3/msdos`、`kmod-md-*`、`kmod-loop`、`kmod-usb-storage*` | 依赖 | 同上 |

### 4.2 feed 中同属 NAS/共享但未进镜像的包（封堵面）

`op-packages` 中还有大量同类包（当前固件未装，但可被用户在线安装，imagebuilder 也可能选装）：

- SMB/NFS/FTP：`luci-app-ksmbd`、`ksmbd-tools`、`luci-app-nfs`、`luci-app-vsftpd`、`vsftpd`、`luci-app-smbuser`、`unishare`
- 下载/PT：`aria2`、`luci-app-qbittorrent(+enhanced)`、`luci-app-transmission`、`luci-app-rtorrent`、`amule`
- 网盘/同步：`luci-app-aliyundrive-webdav`、`luci-app-baidudrive`、`luci-app-clouddrive2`、`luci-app-cloudreve`、`luci-app-openlist2`、`luci-app-syncthing`、`luci-app-nextcloud`
- 文件服务/媒体：`filebrowser(-q)`、`dufs`、`gowebdav`、`webdav2`、`luci-app-emby/jellyfin/plex/navidrome`、`luci-app-dlna-player`
- NAS 面板/主题：`luci-app-nastools`、`luci-app-runmynas`、`luci-theme-inas`、`luci-theme-istorenas`
- 打印（常与 NAS 归一类）：`luci-app-cupsd`、`cups`、`luci-app-usb-printer`

### 4.3 构建脚本中的 NAS 引用点

| 文件 | 位置 | 内容 |
| --- | --- | --- |
| `devices/common/diy.sh` | L42-44 | `DEFAULT_PACKAGES` 注入含 `block-mount`（保留项）、`openssh-sftp-server`（保留项） |
| `devices/common/patches/imagebuilder.patch` | L121-125 | ImageBuilder 组装时：`ifneq ($(findstring usb,$(BUILD_PACKAGES)),) BUILD_PACKAGES += automount luci-app-diskman` —— **只要选了任何含 "usb" 的包就自动追加 NAS 组件**（实测 kmod-usb-net-* 等就会命中） |
| `devices/common/patches/imagebuilder.patch` | L126-135 | 小闪存机型自动 `-luci-app-istorex -htop -bash -openssh-sftp-server -luci-nginx luci-ssl`（说明默认集合里有 istorex） |
| `include/target.mk`（OpenWrt） | `DEFAULT_PACKAGES.nas := block-mount fdisk lsblk mdadm` | 仅 `DEVICE_TYPE=nas` 时生效；x86 的 DEVICE_TYPE=router，**不受影响**，但注意别改成 nas |
| `op-packages/automount/*`、`luci-app-diskman/*`、`luci-app-samba4/*` | 各包 Makefile + files/htdocs | 包定义、LuCI 菜单（`usr/share/luci/menu.d/*.json` 或 lua controller）、hotplug 脚本 |
| `my-default-settings/files/etc/profile.d/30-sysinfo.sh` | L116 | 登录信息里的「系统存储」用量显示（纯文本统计，**无依赖，可留**） |

---

## 5. iStore 相关组件清单与引用位置

### 5.1 镜像中实际存在的 iStore 组件

| 包名 | 角色 | 进入方式 | 依赖链 |
| --- | --- | --- | --- |
| `luci-app-istorex` | iStoreX 整合面板（应用商店入口） | **显式** | `LUCI_DEPENDS:=+luci-app-quickstart +luci-app-store +luci-lib-taskd` |
| `luci-app-store` | iStore 应用商店本体（ipk/apk 商店 UI） | 依赖 | `+curl +tar +libuci-lua +mount-utils +luci-lib-taskd`，且 `@(x86_64\|\|aarch64)` 限定平台 |
| `quickstart` | iStoreOS「快速开始」后端（含网络向导、磁盘探测） | 依赖 | `+shadow-utils +shadow-useradd +mount-utils +block-mount +lsblk +e2fsprogs +parted +smartmontools +smartd +smartmontools-drivedb +mdadm +ip-full +ubus +uci +bash` —— **NAS 磁盘簇的主要拉入者** |
| `luci-app-quickstart` | 快速开始 Web 界面 | 依赖 | `+quickstart +luci-app-ttyd +luci-i18n-ttyd-zh-cn` |
| `taskd` / `luci-lib-taskd` | iStore 任务管理后端/库（商店装包的执行器） | 依赖 | `+procd +script-utils +coreutils`；`luci-lib-taskd` → `+luci-lib-xterm +taskd` |
| `luci-app-ttyd` | 网页终端（quickstart 依赖） | 依赖 | — |

iStore 商店后端文件（随 `luci-app-store` 安装）：

```
/bin/is-opkg                          # 商店的 opkg 封装
/etc/init.d/istore                    # 服务（生成 store id、link_meta）
/etc/config/istore                    # 配置（conffiles）
/usr/libexec/istore/{backup,overlay-backup,docker,mirror-bin/*,ipv4-bin/*}
/usr/share/rpcd/acl.d/luci-app-store.json、/usr/share/ucitrack/luci-app-store.json
src/root-opkg/etc/opkg/compatfeeds.conf   # 商店兼容源
```

### 5.2 feed 中的 iStore 生态（封堵面）

| 包 | 说明 | 依赖要点 |
| --- | --- | --- |
| `istoreenhance` / `luci-app-istoreenhance` | KSpeeder 网络加速 | `+linkease-app-entry +ks-cli` |
| `luci-app-istorego` / `luci-app-istoredup` / `luci-app-istorepanel` | Docker 类扩展面板 | 拉 `docker + dockerd + luci-lib-taskd` |
| `luci-app-istorerouter` | 路由器版面板 | 同 istorex 依赖 |
| `luci-mod-istorenext` | iStoreNext 界面模块 | `+luci-nginxer` |
| `luci-theme-istorenas` / `luci-theme-inas` | NAS 主题 | — |
| `luci-app-substore` | 订阅管理 | — |
| `linkease` / `linkease-app-entry` / `linkease-common-bin` / `linkeasefull` | LinkEase 服务 | 依赖 luci-app-store |
| `quickfile` / `quickjspp` | quickstart 周边 | — |

### 5.3 构建脚本与配置中的 iStore 引用点

| 文件 | 位置 | 内容 |
| --- | --- | --- |
| `devices/common/patches/imagebuilder.patch` | L131 | `BUILD_PACKAGES += -luci-app-istorex ...`（小闪存移除；反证默认集合含 istorex） |
| `devices/*/diy.sh` | amlogic_meson L7、rockchip_armv8 L14 | 仅引用 `istoreos/istoreos` 仓库做**内核/target 移植**，与 iStore 商店无关，**不需要动** |
| `my-default-settings/files/usr/lib/lua/luci/controller/wizard.lua` | L37-66 | 向导路由：`is_running("quickstart")` 才跳 `admin/quickstart`、`admin/istorex/{nas,next-nas,router}` —— **已有运行时防护**，包移除后自动跳过，无需改（可选清理） |
| `op-packages/luci-app-store/`、`taskd/`、`quickstart/`、`luci-app-istorex/` 等 | 各 Makefile | 包定义与依赖 |

---

## 6. 移除方案：改动点清单

### 6.1 方案总览（分层防御）

| 层 | 目的 | 必要性 |
| --- | --- | --- |
| A. 配置黑名单 | 防止 defconfig/DEFAULT 机制把包解析进镜像 | 必做 |
| B. 默认包/脚本检查 | 确认 DEFAULT_PACKAGES 无 NAS/iStore（现状已满足，仅核对） | 必做（核对） |
| C. imagebuilder.patch 修正 | 防止 ImageBuilder/在线组装把 automount/diskman/istorex 拉回来 | 必做（若用 IB 或发布链路） |
| D. feed 封堵（fork op-packages 删包） | 彻底杜绝在线安装/IB 选装回退 | 建议（"彻底移除"时必做） |
| E. 构建缓存清理 | 防止旧包戳文件残留进 rootfs（实测发布固件疑似受此影响） | 必做（首次构建） |

### 6.2 A 层：配置黑名单（核心改动）

在 **`devices/common/.config`** 末尾（或 `devices/x86_64/.config`，作用域按需选择）追加：

```ini
# ===== 精简：移除 NAS 功能 =====
CONFIG_PACKAGE_luci-app-samba4=n
CONFIG_PACKAGE_samba4-server=n
CONFIG_PACKAGE_samba4-libs=n
CONFIG_PACKAGE_samba4-admin=n
CONFIG_PACKAGE_samba4-client=n
CONFIG_PACKAGE_samba4-utils=n
CONFIG_PACKAGE_luci-app-ksmbd=n
CONFIG_PACKAGE_ksmbd-tools=n
CONFIG_PACKAGE_ksmbd-server=n
CONFIG_PACKAGE_automount=n
CONFIG_PACKAGE_ntfs3-mount=n
CONFIG_PACKAGE_luci-app-diskman=n
CONFIG_PACKAGE_luci-app-partexp=n
CONFIG_PACKAGE_mount-utils=n
CONFIG_PACKAGE_mdadm=n
CONFIG_PACKAGE_smartd=n
CONFIG_PACKAGE_smartmontools=n
CONFIG_PACKAGE_smartmontools-drivedb=n
CONFIG_PACKAGE_luci-app-nfs=n
CONFIG_PACKAGE_luci-app-vsftpd=n
CONFIG_PACKAGE_vsftpd=n

# ===== 精简：移除 iStore 应用商店 =====
CONFIG_PACKAGE_luci-app-istorex=n
CONFIG_PACKAGE_luci-app-store=n
CONFIG_PACKAGE_luci-app-quickstart=n
CONFIG_PACKAGE_quickstart=n
CONFIG_PACKAGE_taskd=n
CONFIG_PACKAGE_luci-lib-taskd=n
CONFIG_PACKAGE_luci-app-ttyd=n
CONFIG_PACKAGE_istoreenhance=n
CONFIG_PACKAGE_luci-app-istoreenhance=n
CONFIG_PACKAGE_luci-app-istoredup=n
CONFIG_PACKAGE_luci-app-istorego=n
CONFIG_PACKAGE_luci-app-istorepanel=n
CONFIG_PACKAGE_luci-app-istorerouter=n
CONFIG_PACKAGE_luci-mod-istorenext=n
CONFIG_PACKAGE_luci-app-substore=n
CONFIG_PACKAGE_linkease=n
CONFIG_PACKAGE_linkeasefull=n
```

> 说明：
> - 显式 `=n` 是 kconfig 用户值，优先级高于 `default y if DEFAULT_<pkg>`，可覆盖第②层机制。
> - `samba4-libs/server` 等即使不写也会随 `luci-app-samba4=n` 消失，但显式写上是防依赖闭包意外。
> - 若同时想移除"下载/网盘/媒体服务器"类，可按 §4.2 清单同样加 `=n`。

### 6.3 B 层：DEFAULT_PACKAGES 核对（现状无需改动）

`devices/common/diy.sh:42-44` 注入的默认包为：

```
luci-app-advancedplus luci-app-sysctl luci-app-firewall luci-app-package-manager luci-app-upnp
luci-app-syscontrol luci-app-wizard luci-base luci-compat luci-lib-ipkg luci-lib-fs coremark
wget-ssl curl autocore htop nano zram-swap kmod-lib-zstd kmod-tcp-bbr bash openssh-sftp-server
block-mount resolveip ds-lite swconfig luci-app-fan luci-app-footstrap-files luci-app-wifihistory
```

**其中不含 NAS/iStore**，与本次移除无冲突。`block-mount`、`openssh-sftp-server` 属保留项（§7）。
注意 `luci-app-package-manager` 是 LuCI 自带的 **opkg 软件包管理界面**（不是 iStore），默认保留；
若希望固件完全不可装包，可一并 `CONFIG_PACKAGE_luci-app-package-manager=n`（可选，不影响本方案）。

### 6.4 C 层：修改 `devices/common/patches/imagebuilder.patch`

删除/改写 `target/imagebuilder/files/Makefile` 补丁段中的自动加包逻辑（原 L121-135）：

```diff
-ifneq ($(findstring usb,$(BUILD_PACKAGES)),)
-	ifneq ($(XSMALL_FLASH),true)
-		BUILD_PACKAGES += automount luci-app-diskman
-	endif
-endif
+# 精简固件：不再自动追加 NAS 组件
+#ifneq ($(findstring usb,$(BUILD_PACKAGES)),)
+#	ifneq ($(XSMALL_FLASH),true)
+#		BUILD_PACKAGES += automount luci-app-diskman
+#	endif
+#endif
```

```diff
-		BUILD_PACKAGES += -luci-app-istorex -htop -bash -openssh-sftp-server -luci-nginx luci-ssl
+#		BUILD_PACKAGES += -luci-app-istorex -htop -bash -openssh-sftp-server -luci-nginx luci-ssl
```

（第二处是"小闪存减包"，包删除后 `-luci-app-istorex` 会指向不存在的包；注释掉最稳。
`$(OPKG) install ... || true` 虽然吞错，但事务可能部分失败，不要依赖它。）

### 6.5 D 层：feed 封堵（fork `kiddin9/op-packages`）

1. Fork 后删除目录（镜像内 + 生态，共约 25 个）：

```
luci-app-samba4/  luci-app-ksmbd/  ksmbd-tools/  automount/  luci-app-diskman/
luci-app-partexp/  luci-app-nfs/  luci-app-vsftpd/  vsftpd/
luci-app-store/  luci-app-istorex/  luci-app-quickstart/  quickstart/  taskd/
luci-lib-taskd/  luci-app-ttyd/  istoreenhance/  luci-app-istoreenhance/
luci-app-istoredup/  luci-app-istorego/  luci-app-istorepanel/  luci-app-istorerouter/
luci-mod-istorenext/  luci-app-substore/  linkease/  linkease-app-entry/
linkease-common-bin/  linkeasefull/
```

> 可选：按 §4.2 一并删除下载/网盘/媒体包。

2. 把 `devices/common/diy.sh:5` 的 feed 地址改为自己的 fork：

```bash
sed -i '$a src-git kiddin9 https://github.com/<你>/op-packages.git;main' feeds.conf.default
```

> 若选择保留 feed（不删包），用户仍可通过软件包界面装回；"模块移除"以 A+C 层为准即可，
> D 层是"彻底移除"与"防在线装回"的加强项。

### 6.6 E 层：构建缓存清理

- GitHub Actions：`repo-dispatcher.yml` 支持在触发参数中带 `nocache`
  （`Openwrt-AutoBuild.yml` 的 Cache 步骤 `clean: ${{ contains(github.event.action, 'nocache') }}`）。
  **首次精简构建务必用 `nocache` 触发**，避免 `tmp/.packageinfo` 旧包戳文件把历史包带进 rootfs。
- 本地构建：直接全新 clone + 全新 `build_dir/staging_dir/tmp`，不复用旧构建目录。

---

## 7. 移除后仍需保留的依赖

以下包**不在移除范围**，必须保留，否则会破坏系统基本功能：

| 包 | 保留原因 | 来源 |
| --- | --- | --- |
| `block-mount` | fstab 挂载框架；extroot、Docker 数据盘、普通 U 盘手动挂载都依赖它；也被 diy.sh 置于 DEFAULT_PACKAGES | `DEFAULT_PACKAGES` |
| `e2fsprogs`、`mkf2fs`、`partx-utils`、`kmod-fs-f2fs`、`kmod-fs-ext4` | x86 根分区/引导分区工具链（`target/linux/x86/Makefile` 的 DEFAULT_PACKAGES），**不是 NAS 专属** | OpenWrt x86 target |
| `kmod-fs-vfat`、`dosfstools`（可选保留） | ESP/启动分区与 U 盘兼容；dosfstools 原由 diskman 拉入，如无 U 盘格式化需求可不保 | 设备包/依赖 |
| `openssh-sftp-server` | SSH 文件传输组件，属 SSH 服务而非 NAS 共享 | `DEFAULT_PACKAGES` |
| `bash`、`curl`、`wget-ssl`、`htop`、`nano`、`coremark`、`autocore` 等 | 基础体验包，与 NAS/iStore 无关 | `DEFAULT_PACKAGES` |
| `luci-app-wizard` | 设置向导（独立于 quickstart/iStoreX）；其内部对 istorex/quickstart 的跳转有 `is_running()` 防护 | `DEFAULT_PACKAGES` |
| `luci-app-package-manager` | LuCI 自带 opkg 管理界面，不是 iStore；如需"完全不可装包"再移除 | `DEFAULT_PACKAGES` |
| `kmod-usb-storage*`（由 automount 拉入） | 移除 automount 后自然消失；仅当确需 USB 存储手动挂载时才需回装 `kmod-usb-storage`（此时 block-mount 已足够做手动挂载） | 依赖 |

**特别提示**：`quickstart` 的依赖里含 `bash`、`ip-full`、`ubus`、`uci`、`shadow-utils` 等通用件，
移除 quickstart 只是让它们不再被"顺带拉入"，上述通用件在 DEFAULT_PACKAGES 或其它路径仍在，**不会缺包**。

---

## 8. 风险与副作用评估

| # | 风险 | 影响 | 处理建议 |
| --- | --- | --- | --- |
| 1 | Web 界面残留入口 | 低 | LuCI 菜单由各包自带的 `menu.d/*.json` / lua controller 注册，包移除即入口消失。`my-default-settings` 的 wizard.lua 对 quickstart/istorex 路由有 `is_running("quickstart")` 运行时判断，自动降级到默认页，**不报错**。可选清理 `wizard.lua` L57-66 的 istore_routes 段 |
| 2 | LuCI 依赖缺失导致编译失败 | 低 | 被移除的都是叶子应用层包，无任何 `=y` 包反向依赖它们（feed 内 grep 验证）。`=n` 后包仍可作为 `.ipk` 被 `CONFIG_ALL_NONSHARED=y` 编译出来（喂软件源），不影响编译通过 |
| 3 | 升级兼容（旧 overlay 残留） | 中 | `sysupgrade` 保留配置时，overlay 里以前 `opkg install` 的 samba/iStore 文件与配置会**继续存在**。两种处理：① 刷精简固件时**不保留配置**（或刷后 `firstboot`）；② 升级前先 `opkg remove luci-app-istorex luci-app-store quickstart taskd luci-app-samba4 samba4-server automount luci-app-diskman` 再升级 |
| 4 | ImageBuilder/在线定制回流 | 中 | 不改 `imagebuilder.patch` 时，IB 组装遇 usb 包会自动 `+automount luci-app-diskman`；feed 删包后 IB 事务会报找不到包（`\|\| true` 掩盖错误但产物不可靠）。**必须同步做 C 层** |
| 5 | 构建缓存残留 | 中 | 历史构建的 `tmp/.packageinfo/*.install` 戳文件可能把旧包列入安装清单（发布固件 470 包与 .config 不一致即疑似此因）。首次构建走 `nocache`/全新目录，并用 manifest 验证（§10） |
| 6 | 在线装回 | 低 | feed 保留时用户可经软件包界面重装。需要彻底封死就做 D 层 fork 删包 |
| 7 | quickstart 的双面性 | 低 | quickstart 既是 iStoreOS 向导也是"快速开始"面板；移除后首次开机走 `luci-app-wizard` 的 initsetup 即可，功能不缺 |
| 8 | 平台条件差异 | 低 | `luci-app-store` 有 `@(x86_64\|\|aarch64)` 限定；在 ramips/ath79 等小目标上它本就不参与，黑名单写上也无副作用 |
| 9 | `DEVICE_TYPE=nas` 误用 | 低 | OpenWrt 25.12 的 `include/target.mk` 有 `DEFAULT_PACKAGES.nas`（block-mount/fdisk/lsblk/mdadm）。Kwrt 的 x86 用的是 router 类型；**不要**在自定义 target.mk 里设置 `DEVICE_TYPE:=nas` |

---

## 9. 最终执行步骤清单

以**干净源码、x86_64 目标**为例（其他 target 把 `x86_64` 换成对应目录即可）。

### 9.1 准备

```bash
# 1. Fork 并克隆构建仓库（建议 fork 后改名，如 kwrt-slim）
git clone https://github.com/<你>/Kwrt kwrt-slim && cd kwrt-slim

# 2. （可选但建议）Fork op-packages 并删除 §6.5 的包目录，供 D 层使用
```

### 9.2 修改（对应 §6）

```bash
# 3. A 层：配置黑名单
cat >> devices/common/.config <<'EOF'
# ===== 精简：移除 NAS 功能 =====
CONFIG_PACKAGE_luci-app-samba4=n
CONFIG_PACKAGE_samba4-server=n
CONFIG_PACKAGE_samba4-libs=n
CONFIG_PACKAGE_luci-app-ksmbd=n
CONFIG_PACKAGE_ksmbd-tools=n
CONFIG_PACKAGE_ksmbd-server=n
CONFIG_PACKAGE_automount=n
CONFIG_PACKAGE_ntfs3-mount=n
CONFIG_PACKAGE_luci-app-diskman=n
CONFIG_PACKAGE_luci-app-partexp=n
CONFIG_PACKAGE_mount-utils=n
CONFIG_PACKAGE_mdadm=n
CONFIG_PACKAGE_smartd=n
CONFIG_PACKAGE_smartmontools=n
CONFIG_PACKAGE_smartmontools-drivedb=n
CONFIG_PACKAGE_luci-app-nfs=n
CONFIG_PACKAGE_luci-app-vsftpd=n
CONFIG_PACKAGE_vsftpd=n
# ===== 精简：移除 iStore 应用商店 =====
CONFIG_PACKAGE_luci-app-istorex=n
CONFIG_PACKAGE_luci-app-store=n
CONFIG_PACKAGE_luci-app-quickstart=n
CONFIG_PACKAGE_quickstart=n
CONFIG_PACKAGE_taskd=n
CONFIG_PACKAGE_luci-lib-taskd=n
CONFIG_PACKAGE_luci-app-ttyd=n
CONFIG_PACKAGE_istoreenhance=n
CONFIG_PACKAGE_luci-app-istoreenhance=n
CONFIG_PACKAGE_luci-app-istoredup=n
CONFIG_PACKAGE_luci-app-istorego=n
CONFIG_PACKAGE_luci-app-istorepanel=n
CONFIG_PACKAGE_luci-app-istorerouter=n
CONFIG_PACKAGE_luci-mod-istorenext=n
CONFIG_PACKAGE_luci-app-substore=n
CONFIG_PACKAGE_linkease=n
CONFIG_PACKAGE_linkeasefull=n
EOF

# 4. C 层：注释 imagebuilder.patch 的自动加包段（§6.4 的两处 diff）
# 5. D 层（可选）：devices/common/diy.sh L5 改为自己的 op-packages fork
```

### 9.3 编译（两条路径任选）

**路径一：GitHub Actions（与官方一致）**

```bash
git add -A && git commit -m "feat: 精简固件，移除 NAS 与 iStore 模块" && git push
# Actions → Repo Dispatcher → Run workflow，param 填：
#   x86_64 nocache     ← 首次必须带 nocache
# 也可用 API 派发：event_type: "x86_64 nocache", client_payload: {"target":"x86_64"}
```

**路径二：本地 Linux（WSL2/Debian，磁盘 ≥ 40GB）**

```bash
sudo apt-get install build-essential clang flex bison g++ gawk gcc-multilib \
  g++-multilib gettext git libncurses5-dev libssl-dev python3-setuptools rsync \
  swig unzip zlib1g-dev file wget dwarves llvm python3-pyelftools libpython3-dev \
  jq qemu-utils ccache libelf-dev device-tree-compiler libgmp3-dev libmpc-dev libfuse-dev

git clone https://github.com/openwrt/openwrt -b openwrt-25.12 openwrt
cp -rf devices/common/. openwrt/
cp -rf devices/x86_64/. openwrt/
cp -rf devices openwrt/
cd openwrt
chmod +x devices/common/diy.sh
/bin/bash devices/common/diy.sh                 # 装 feed、注入默认包（与 CI 相同）
cp -f devices/common/.config .config
echo >> .config && cat devices/x86_64/.config >> .config
chmod +x devices/x86_64/diy.sh && /bin/bash devices/x86_64/diy.sh
cp -Rf ./diy/* ./ || true
# 应用补丁（与 CI 相同）：
cp -rn devices/common/patches devices/x86_64/
find devices/x86_64/patches -maxdepth 1 -type f -name '*.patch' ! -name '*.revert.patch' ! -name '*.bin.patch' \
  -print0 | sort -z | xargs -I % -t -0 -n 1 sh -c "patch -d './' --no-backup-if-mismatch -p1 -F 1 -i '%'"
make defconfig
make -j$(($(nproc)+1)) || make V=s
```

### 9.4 验证（必做）

```bash
# 产物位于 bin/targets/x86/64/
grep -E 'samba|istore|quickstart|taskd|automount|diskman|mdadm|smartmontools|partexp|mount-utils|ntfs3' \
  bin/targets/x86/64/*.manifest
# 期望：无任何输出

# 或直接查 rootfs 的安装状态（最可靠）
tar -xzf bin/targets/x86/64/*rootfs.tar.gz ./usr/lib/opkg/status -O | \
  grep -E '^Package: (samba|luci-app-(store|istore|quickstart|samba|diskman)|automount|quickstart|taskd|mdadm|smart)'
# 期望：无任何输出
```

### 9.5 刷机注意

- 从完整 Kwrt 升级而来：**不保留配置**刷入（或刷后 `firstboot`），避免 overlay 残留旧插件文件。
- 全新安装：直接写盘即可，LuCI 中将不再出现「网络存储 / iStore / 应用商店」相关菜单。

---

## 10. 附录：构建后验证方法

| 验证对象 | 方法 | 通过标准 |
| --- | --- | --- |
| 镜像清单 | `grep -E 'samba\|istore\|quickstart\|taskd\|automount\|diskman\|mdadm\|smartmon' bin/targets/*/*.manifest` | 空输出 |
| rootfs 状态 | rootfs tar 中 `usr/lib/opkg/status` 同上 grep | 空输出 |
| 固件体积 | 对比精简前后 `*-squashfs-combined*.img.gz` | 预计减少约 8-15MB（samba4 + istore 全家桶 + mdadm/smartmontools 等） |
| LuCI 菜单 | 刷机后登录 → 服务/网络存储菜单 | 无「网络存储」「iStore」「快速开始（istorex）」入口 |
| 依赖完整性 | `make defconfig` 后检查 `.config` 无 `is not set` 导致的连锁缺失；编译全程无 `WARNING: skipping` 以外的错误 | 编译成功出固件 |
| ImageBuilder（可选） | 本地 `make image PROFILE=generic PACKAGES="luci-app-wifi"` 测试 IB 不再拉 automount/diskman | manifest 无 NAS 包 |

---

## 附：分析过程留存的关键证据文件（本机）

| 文件 | 说明 |
| --- | --- |
| `Kwrt/` | 官方构建仓库浅克隆 |
| `op-packages/` | kiddin9 自定义 feed 浅克隆（893 包） |
| `manifest-x86.txt` | 官方发布固件的包清单（470 包） |
| `full-config-x86_64` / `ib-.config` | 构建完整 .config（两份一致） |
| `ib-.targetinfo` / `ib-.packageinfo` | ImageBuilder 目标/包元数据（Default-Packages、依赖） |
| `ci-rootfs-opkg-status` | 发布 rootfs 的 opkg 状态（显式/依赖标志分析依据） |

---

## 11. 执行记录（2026-10-21 已落地）

方案已实际改动到工作区并提交，与 §6 的差异如下：

### 11.1 已完成改动

| 层 | 状态 | 提交 |
| --- | --- | --- |
| A. 配置黑名单 | ✅ `devices/common/.config` 末尾追加 37 条 `=n`（§6.2 全量清单，含 samba4-admin/client/utils） | `Kwrt@b07b4d0` |
| B. DEFAULT_PACKAGES 核对 | ✅ 现状无 NAS/iStore，未改动 | — |
| C. imagebuilder.patch | ✅ usb→automount/diskman 段已整体注释；小闪存行已去掉 `-luci-app-istorex` | `Kwrt@b07b4d0` |
| D. feed 封堵 | ✅ 本地 op-packages 已删 29 个包目录（582 文件 / -122479 行）并提交 | `op-packages@f335bcbb` |
| E. 缓存清理 | 构建时执行（首次必须 `nocache` 触发） | — |

### 11.2 与 §6 原文的偏差（有意为之）

1. **C 层第二处**：未整行注释小闪存减包行，只去掉 `-luci-app-istorex` 令牌，保留
   `-htop -bash -openssh-sftp-server -luci-nginx luci-ssl`。原因：该行的 `-` 项在
   `BUILD_PACKAGES` 第二次 `filter-out`（make 层）即被剥离、根本到不了 opkg，整行注释反而会让
   小闪存机型丢失减包/luci-ssl 行为；去掉 istorex 令牌即可消除对已删包的引用。
2. **D 层保留三个共享库**（§6.5 原清单含 taskd/linkease 全家）：
   - `taskd`、`luci-lib-taskd`：被 **50+ 个应用包**反向依赖（含 drawio、ittools、memos、
     vaultwarden、uptimekuma、typecho 等非 NAS 通用应用），删除会大面积破坏可安装性；
   - `linkease-app-entry`：被 agentflow、baidudrive、dockermanager、fastnet、kai、kaiplus 用作应用入口网关。
   三者均已进 A 层黑名单，不会进入固件；仅影响用户日后经 opkg 主动加装。
3. **D 层额外删除** `luci-app-linkease`、`luci-lib-linkeasefile`、`luci-app-linkeasefull`、
   `luci-app-linkeasefull-embed`、`linkease-runtime-transition`（LinkEase 文件云的 UI/生态壳，
   其父包删除后本就不可选）；精确依赖分析证实删除不影响上述 6 个应用。
4. `luci-app-ttyd` 不在 op-packages 中（来自其他 feed），无需删，A 层黑名单已覆盖。

### 11.3 D 层生效的最后一步（需 GitHub 操作）

本地 op-packages 精简版需推到自己的 fork 后，把 `devices/common/diy.sh:5` 改为：

```bash
sed -i '$a src-git kiddin9 https://github.com/<你>/op-packages.git;main' feeds.conf.default
```

不改也可构建（A+C 已保证固件精简），只是 feed 里仍可在线装回。

### 11.4 已做验证

- `imagebuilder.patch` 全部 hunk 行数与头部一致；对 openwrt-25.12 源码 `patch -p1 -F 1`
  干跑与实际应用均通过，产物中 usb 段为注释态、`BUILD_PACKAGES += -htop -bash ...` 无 istorex。
- 黑名单 37 个包名对照 `.packageinfo` 全部真实存在（仅 `ksmbd-tools` 不在索引，`=n` 无害保留）。
- 首次构建请用 `nocache` 触发，构建后按 §9.4 / §10 验证 manifest。
