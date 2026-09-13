ARCHS := arm64
TARGET := iphone:clang:16.5:15.0
include $(THEOS)/makefiles/common.mk
TOOL_NAME := trollvncserver
trollvncserver_USE_MODULES := 0
trollvncserver_FILES := src/main.mm src/MirrorServer.mm src/MirrorConnection.mm src/MirrorCapture.mm src/MirrorInput.mm src/HEVCEncoder.mm src/OhMyJetsam.mm
trollvncserver_CFLAGS := -fobjc-arc -Iinclude -Ivendor -Wall -Wextra
trollvncserver_CCFLAGS := -std=c++20
trollvncserver_FRAMEWORKS := Foundation UIKit QuartzCore CoreGraphics CoreVideo CoreMedia IOSurface IOKit VideoToolbox
trollvncserver_PRIVATE_FRAMEWORKS := FrontBoardServices
trollvncserver_CODESIGN_FLAGS := -Strollvncserver.entitlements
include $(THEOS_MAKE_PATH)/tool.mk
