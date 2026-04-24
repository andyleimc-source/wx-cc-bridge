.PHONY: sync echo run install-service uninstall-service restart-service status logs

PLIST_LABEL    := com.wx-cc-bridge
PLIST_TEMPLATE := scripts/wx-cc-bridge.plist.template
PLIST_DST      := $(HOME)/Library/LaunchAgents/$(PLIST_LABEL).plist
LOG_DIR        := $(HOME)/Library/Logs/wx-cc-bridge

# uv's editable .pth gets UF_HIDDEN'd on macOS and something keeps re-hiding it
# even after chflags, so we sidestep the whole mess via PYTHONPATH=src. See bug.md.
sync:
	uv sync

echo:
	PYTHONPATH=src .venv/bin/python -m wx_cc_bridge.echo

run:
	PYTHONPATH=src .venv/bin/python -m wx_cc_bridge.bridge

install-service:
	@mkdir -p $(LOG_DIR) $(HOME)/Library/LaunchAgents
	@sed -e 's|{{REPO}}|$(CURDIR)|g' -e 's|{{HOME}}|$(HOME)|g' $(PLIST_TEMPLATE) > $(PLIST_DST)
	launchctl unload $(PLIST_DST) 2>/dev/null || true
	launchctl load -w $(PLIST_DST)
	@echo "✓ installed. logs at $(LOG_DIR)/"
	@echo "  make status  # check running"
	@echo "  make logs    # tail logs"

uninstall-service:
	launchctl unload $(PLIST_DST) 2>/dev/null || true
	rm -f $(PLIST_DST)
	@echo "✓ uninstalled"

restart-service:
	launchctl kickstart -k gui/$(shell id -u)/$(PLIST_LABEL)
	@echo "✓ restarted"

status:
	@launchctl list | grep $(PLIST_LABEL) || echo "(not running)"

logs:
	@tail -n 50 -f $(LOG_DIR)/bridge.log $(LOG_DIR)/bridge.err.log
