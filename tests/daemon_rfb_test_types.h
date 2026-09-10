#pragma once
#include <cstddef>
#include <cstdint>
#include <atomic>
#include <pthread.h>
typedef bool rfbBool;
struct rfbPixelFormat { uint8_t bitsPerPixel=32, depth=24, bigEndian=0, trueColour=1; uint16_t redMax=255,greenMax=255,blueMax=255; uint8_t redShift=16,greenShift=8,blueShift=0; };
struct rfbClient { pthread_mutex_t sendMutex=PTHREAD_MUTEX_INITIALIZER; std::atomic<int> refs{0}; int sock=1; };
struct rfbScreenInfo { char *frameBuffer=nullptr; int width=0,height=0,paddedWidthInBytes=0; rfbPixelFormat serverFormat; };
using rfbClientPtr=rfbClient *;
using rfbScreenInfoPtr=rfbScreenInfo *;
rfbBool rfbGetClientSnapshot(rfbScreenInfoPtr, rfbClientPtr **, size_t *);
void rfbReleaseClientSnapshot(rfbClientPtr *, size_t);
rfbBool rfbNewFramebufferWithFormat(rfbScreenInfoPtr,char *,int,int,int,int,int,const rfbPixelFormat *);
void rfbMarkRectAsModified(rfbScreenInfoPtr,int,int,int,int);
