# SPDX-License-Identifier: MIT
#
# mcp-routerd - MCP (Model Context Protocol) server for OpenWrt routers
# Exposes router capabilities to AI agents over HTTP + JSON-RPC 2.0.

include $(TOPDIR)/rules.mk

PKG_NAME:=mcp-routerd
PKG_VERSION:=1.0.0
PKG_RELEASE:=1

PKG_MAINTAINER:=jianjunx <jianjun@agent.qq.com>
PKG_LICENSE:=MIT

include $(INCLUDE_DIR)/package.mk

define Package/mcp-routerd
  SECTION:=net
  CATEGORY:=Network
  TITLE:=MCP server exposing router capabilities to AI agents
  DEPENDS:=+ucode +ucode-mod-ubus +ucode-mod-uci +ucode-mod-socket +ucode-mod-fs +ucode-mod-log
endef

define Package/mcp-routerd/description
  JSON-RPC 2.0 based MCP (Model Context Protocol) server for AI agents.
  Read-only tools (system/network/wifi/firewall/clients/logs) are always
  available; write tools are gated behind uci allow_write plus an explicit
  confirm parameter. Bearer token auth, audit logging via syslog.
endef

define Package/mcp-routerd/conffiles
/etc/config/mcp
endef

define Build/Compile
endef

define Package/mcp-routerd/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) ./files/usr/bin/mcp-routerd $(1)/usr/bin/
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/etc/init.d/mcp-routerd $(1)/etc/init.d/
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./files/etc/config/mcp $(1)/etc/config/
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/etc/uci-defaults/99-mcp-routerd $(1)/etc/uci-defaults/
	$(INSTALL_DIR) $(1)/etc/nginx/conf.d
	$(INSTALL_DATA) ./files/etc/nginx/conf.d/mcp-routerd.locations $(1)/etc/nginx/conf.d/
endef

$(eval $(call BuildPackage,mcp-routerd))
