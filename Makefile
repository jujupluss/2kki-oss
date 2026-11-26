# Yume 2kki OSS — build & extraction pipeline
#
#   make extract   extract original/ into build/game + xml/   (needs lcf2xml)
#   make data      rebuild build/game LCF from edited xml/
#   make player    build EasyRPG Player into build/player
#   make run       run the game from build/game
#   make verify    sanity-check the extracted tree
#   make audit     resolve every asset reference, report missing/unused
#   make map-tree  analyse/prune the map tree (report only)
#   make prune-tree apply the pruned map tree and rebuild RPG_RT.lmt
#   make translate generate translation/<lang>/*.po (EasyRPG Player)
#   make run-en    run the game with the English translation
#   make patch-liblcf apply the optional upstream CR fix and rebuild
#   make content-* content baseline/status/export/apply/check
#   make test      run the test suite (no game data needed)
#   make clean     remove build/ and xml/
#
# Everything is derived from original/ which is read-only and never modified.

SHELL   := /bin/bash
PYTHON  ?= python3
JOBS    ?= $(shell nproc 2>/dev/null || echo 4)

BUILD   ?= build
GAME    ?= $(BUILD)/game
XML     ?= xml
PREFIX  ?= $(BUILD)/prefix
TRANSLATION ?= translation
TR_LANG ?= en

LCF2XML        ?= $(BUILD)/liblcf/lcf2xml
PLAYER         ?= $(BUILD)/player/easyrpg-player
LIBLCF_CONFIG  ?= $(PREFIX)/lib/cmake/liblcf/liblcf-config.cmake
MANIFEST       ?= $(BUILD)/manifest.json
CONTENT_ARGS    = --xml $(XML) --content content --manifest $(MANIFEST) \
                  --original original --lcf2xml $(LCF2XML)

CMAKE_FLAGS := -G Ninja -DCMAKE_BUILD_TYPE=Release

.PHONY: all extract xml data player run run-en verify audit map-tree prune-tree \
	translate patch-liblcf content-baseline content-status content-export \
	content-apply content-check ensure-extracted submodules test clean \
	distclean help

all: extract

help:
	@sed -n '1,/^$$/p' Makefile | sed 's/^# \{0,1\}//'

# ------------------------------------------------------------ submodules ---
# A fresh clone without --recursive has empty vendor/ dirs.
LIBLCF_SRC := vendor/liblcf/CMakeLists.txt
PLAYER_SRC := vendor/Player/CMakeLists.txt

$(LIBLCF_SRC) $(PLAYER_SRC):
	git submodule update --init vendor/liblcf vendor/Player

submodules: $(LIBLCF_SRC) $(PLAYER_SRC)

# ---------------------------------------------------------------- liblcf ---
$(LCF2XML): | $(LIBLCF_SRC)
	@mkdir -p $(BUILD)
	cmake -S vendor/liblcf -B $(BUILD)/liblcf $(CMAKE_FLAGS) \
		-DBUILD_SHARED_LIBS=OFF \
		-DLIBLCF_ENABLE_TESTS=OFF \
		-DLIBLCF_ENABLE_BENCHMARKS=OFF \
		-DLIBLCF_ENABLE_INSTALL=ON \
		-DCMAKE_INSTALL_PREFIX=$(abspath $(PREFIX))
	cmake --build $(BUILD)/liblcf --target lcf2xml -j$(JOBS)

$(LIBLCF_CONFIG): $(LCF2XML)
	cmake --build $(BUILD)/liblcf --target install -j$(JOBS)

# --------------------------------------------------------------- extract ---
extract: $(LCF2XML)
	$(PYTHON) scripts/extract.py --src original --game $(GAME) --xml $(XML) \
		--lcf2xml $(LCF2XML) --manifest $(MANIFEST)

# Only extract when the derived tree is missing: re-running extract would
# overwrite the pruned map tree / rebuilt content in build/game.
ensure-extracted:
	@test -f $(GAME)/RPG_RT.ldb -a -f $(XML)/RPG_RT.edb \
		|| $(MAKE) --no-print-directory extract

# only refresh XML from an already-extracted game dir
xml: $(LCF2XML)
	$(PYTHON) scripts/extract.py --xml-only \
		--game $(GAME) --xml $(XML) --lcf2xml $(LCF2XML) --force-xml

# -------------------------------------------------------- liblcf patch ---
# Optional: fix the upstream XML writer so CR survives without post-processing.
LIBLCF_PATCH := $(abspath patches/liblcf-escape-cr.patch)

patch-liblcf: | $(LIBLCF_SRC)
	@if git -C vendor/liblcf apply --check $(LIBLCF_PATCH) 2>/dev/null; then \
		git -C vendor/liblcf apply $(LIBLCF_PATCH) && echo "applied liblcf CR patch"; \
	elif git -C vendor/liblcf apply --reverse --check $(LIBLCF_PATCH) 2>/dev/null; then \
		echo "liblcf CR patch already applied"; \
	else \
		echo "error: liblcf CR patch does not apply to vendor/liblcf" >&2; exit 1; \
	fi
	@rm -f $(LCF2XML)
	@$(MAKE) --no-print-directory $(LCF2XML)

# ---------------------------------------------------------------- data ---
data: $(LCF2XML)
	$(PYTHON) scripts/rebuild.py --xml $(XML) --game $(GAME) --lcf2xml $(LCF2XML)

# ---------------------------------------------------------------- player ---
$(PLAYER): $(LIBLCF_CONFIG) | $(PLAYER_SRC)
	cmake -S vendor/Player -B $(BUILD)/player $(CMAKE_FLAGS) \
		-DCMAKE_PREFIX_PATH=$(abspath $(PREFIX)) \
		-DPLAYER_TARGET_PLATFORM=SDL2 \
		-DPLAYER_BUILD_LIBLCF=OFF \
		-DPLAYER_ENABLE_TESTS=OFF
	cmake --build $(BUILD)/player -j$(JOBS)

player: $(PLAYER)

run: ensure-extracted $(PLAYER)
	$(PLAYER) --game $(GAME)

# ----------------------------------------------------------- translation ---
translate: ensure-extracted
	$(PYTHON) scripts/translate.py --xml $(XML) --out $(TRANSLATION) --lang $(TR_LANG)

run-en: ensure-extracted translate $(PLAYER)
	$(PLAYER) --game $(GAME) --language-path $(TRANSLATION) --language $(TR_LANG)

# ---------------------------------------------------------------- verify ---
verify: ensure-extracted
	$(PYTHON) scripts/verify.py --game $(GAME) --xml $(XML)

# ---------------------------------------------------------------- audit ---
audit: ensure-extracted
	$(PYTHON) scripts/audit.py --game $(GAME) --xml $(XML)

# ------------------------------------------------------------- map tree ---
map-tree: ensure-extracted
	$(PYTHON) scripts/map_tree.py --lmt $(XML)/RPG_RT.emt --game $(GAME)

prune-tree: ensure-extracted $(LCF2XML)
	$(PYTHON) scripts/map_tree.py --lmt $(XML)/RPG_RT.emt --game $(GAME) --apply
	$(PYTHON) scripts/rebuild.py --xml $(XML) --game $(GAME) --lcf2xml $(LCF2XML)

# --------------------------------------------------------------- content ---
# All of these need xml/; export and check also regenerate baseline XML
# from original/ with lcf2xml and build/manifest.json.
content-baseline content-status content-apply: ensure-extracted
	$(PYTHON) scripts/content.py $(@:content-%=%) $(CONTENT_ARGS)

content-export content-check: ensure-extracted $(LCF2XML)
	$(PYTHON) scripts/content.py $(@:content-%=%) $(CONTENT_ARGS)

# ----------------------------------------------------------------- tests ---
test:
	$(PYTHON) -m pytest

# ----------------------------------------------------------------- clean ---
clean:
	rm -rf $(BUILD) $(XML)

distclean: clean
	rm -rf vendor/Player/lib
