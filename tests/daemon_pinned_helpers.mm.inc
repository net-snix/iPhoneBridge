/* Unchanged source excerpts from TrollVNC commit
 * a3e40816ea5b93a7c80c09625175893d15bd1070, src/trollvncserver.mm.
 * Copyright (c) 2025 82Flex <82flex@gmail.com> and contributors.
 * GPL version 2 only; https://www.gnu.org/licenses/old-licenses/gpl-2.0.html
 * Source: https://github.com/owngoal-dev/TrollVNC/blob/a3e40816ea5b93a7c80c09625175893d15bd1070/src/trollvncserver.mm
 *
 * These pixel/CRC helpers are byte-identical to that pinned source and to the
 * candidate. Changed producer, publisher, resize, dirty-rectangle, completion,
 * and lifecycle bodies are always extracted from the canonical patch instead.
 */
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
NS_INLINE uint64_t crc32_update(uint64_t h, const uint8_t *data, size_t len) {
    uint32_t c = (uint32_t)h;
    const uint8_t *p = data;
    size_t n = len;
    // Process 8-byte chunks
    while (n >= 8) {
        uint64_t v;
        // Unaligned load is acceptable on ARM64; use memcpy to be safe for strict aliasing.
        memcpy(&v, p, sizeof(v));
        c = __builtin_arm_crc32d(c, v);
        p += 8;
        n -= 8;
    }
    if (n >= 4) {
        uint32_t v32;
        memcpy(&v32, p, sizeof(v32));
        c = __builtin_arm_crc32w(c, v32);
        p += 4;
        n -= 4;
    }
    if (n >= 2) {
        uint16_t v16;
        memcpy(&v16, p, sizeof(v16));
        c = __builtin_arm_crc32h(c, v16);
        p += 2;
        n -= 2;
    }
    if (n) {
        c = __builtin_arm_crc32b(c, *p);
    }
    return (uint64_t)c;
}
#else
NS_INLINE uint64_t fnv1a_basis(void) { return 1469598103934665603ULL; }
NS_INLINE uint64_t fnv1a_update(uint64_t h, const uint8_t *data, size_t len) {
    const uint64_t FNV_PRIME = 1099511628211ULL;
    for (size_t i = 0; i < len; ++i) {
        h ^= (uint64_t)data[i];
        h *= FNV_PRIME;
    }
    return h;
}
#endif

// Generic hash wrappers: prefer hardware CRC32 when enabled and available, else fallback to FNV-1a.
NS_INLINE uint64_t hash_basis(void) {
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
    return 0u; // CRC32 initial accumulator
#else
    return fnv1a_basis();
#endif
}

NS_INLINE uint64_t hash_update(uint64_t h, const uint8_t *data, size_t len) {
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
    return crc32_update(h, data, len);
#else
    // If CRC32 not supported at compile time, fallback to FNV-1a
    return fnv1a_update(h, data, len);
#endif
}


static void initializeTilingOrReset(void) {
    int tilesX = (gWidth + gTileSize - 1) / gTileSize;
    int tilesY = (gHeight + gTileSize - 1) / gTileSize;
    size_t tileCount = (size_t)tilesX * (size_t)tilesY;

    if (tilesX != gTilesX || tilesY != gTilesY || tileCount != gTileCount || !gPrevHash || !gCurrHash) {
        free(gPrevHash);
        free(gCurrHash);

        if (gPendingDirty) {
            free(gPendingDirty);
            gPendingDirty = NULL;
        }

        gPrevHash = (uint64_t *)malloc(tileCount * sizeof(uint64_t));
        gCurrHash = (uint64_t *)malloc(tileCount * sizeof(uint64_t));
        gPendingDirty = (uint8_t *)malloc(tileCount);

        if (!gPrevHash || !gCurrHash) {
            TVPrintError("Out of memory for tile hashes");
            exit(EXIT_FAILURE);
        }

        for (size_t i = 0; i < tileCount; ++i) {
            gPrevHash[i] = 0; // force full update first frame
            gCurrHash[i] = hash_basis();
        }

        gTilesX = tilesX;
        gTilesY = tilesY;
        gTileCount = tileCount;

        if (gPendingDirty)
            memset(gPendingDirty, 0, gTileCount);
    } else {
        for (size_t i = 0; i < gTileCount; ++i) {
            gCurrHash[i] = hash_basis();
        }
    }
}

NS_INLINE void swapTileHashes(void) {
    uint64_t *tmp = gPrevHash;
    gPrevHash = gCurrHash;
    gCurrHash = tmp;
}

NS_INLINE void resetCurrTileHashes(void) {
    if (!gCurrHash || gTileCount == 0)
        return;
    uint64_t basis = hash_basis();
    for (size_t i = 0; i < gTileCount; ++i) {
        gCurrHash[i] = basis;
    }
}

NS_INLINE void accumulatePendingDirty(void) {
    if (!gPendingDirty)
        return;

    for (size_t i = 0; i < gTileCount; ++i) {
        if (gCurrHash[i] != gPrevHash[i])
            gPendingDirty[i] = 1;
    }
}

NS_INLINE void hashTiledFromBuffer(const uint8_t *buf, int width, int height, size_t bpr) {
    resetCurrTileHashes();
    for (int y = 0; y < height; ++y) {
        int ty = y / gTileSize;
        for (int tx = 0; tx < gTilesX; ++tx) {
            int startX = tx * gTileSize;
            if (startX >= width)
                break;
            int endX = startX + gTileSize;
            if (endX > width)
                endX = width;
            size_t offset = (size_t)startX * (size_t)gBytesPerPixel;
            size_t length = (size_t)(endX - startX) * (size_t)gBytesPerPixel;
            size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
            gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], buf + (size_t)y * bpr + offset, length);
        }
    }
}

NS_INLINE void hashTiledFromBufferParallel(const uint8_t *buf, int width, int height, size_t bpr, int threads) {
    if (threads <= 1) {
        hashTiledFromBuffer(buf, width, height, bpr);
        return;
    }
    resetCurrTileHashes();
    // Split by tile row bands
    int tilesY = gTilesY;
    if (tilesY <= 0)
        return;
    int bands = threads;
    if (bands > tilesY)
        bands = tilesY;
    dispatch_group_t grp = dispatch_group_create();
    for (int band = 0; band < bands; ++band) {
        dispatch_group_async(grp, dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            for (int ty = band; ty < tilesY; ty += bands) {
                int startY = ty * gTileSize;
                int endY = startY + gTileSize;
                if (startY >= height)
                    break;
                if (endY > height)
                    endY = height;
                for (int y = startY; y < endY; ++y) {
                    for (int tx = 0; tx < gTilesX; ++tx) {
                        int startX = tx * gTileSize;
                        if (startX >= width)
                            break;
                        int endX = startX + gTileSize;
                        if (endX > width)
                            endX = width;
                        size_t offset = (size_t)startX * (size_t)gBytesPerPixel;
                        size_t length = (size_t)(endX - startX) * (size_t)gBytesPerPixel;
                        size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
                        // Each tileIndex is updated by a single band (fixed ty), no race across bands.
                        gCurrHash[tileIndex] =
                            hash_update(gCurrHash[tileIndex], buf + (size_t)y * bpr + offset, length);
                    }
                }
            }
        });
    }
    dispatch_group_wait(grp, DISPATCH_TIME_FOREVER);
}

NS_INLINE void alignDimensions(int rawW, int rawH, int *alignedW, int *alignedH) {
    if (rawW <= 0)
        rawW = 1;
    if (rawH <= 0)
        rawH = 1;
    // Round width up to next multiple of 4
    int w4 = (rawW + 3) & ~3;
    long long numer = (long long)rawH * (long long)w4;
    int hAdj = (int)((numer + rawW / 2) / rawW); // rounded to nearest
    if (hAdj <= 0)
        hAdj = 1;
    *alignedW = w4;
    *alignedH = hAdj;
}

NS_INLINE void copyWithStrideTight(uint8_t *dstTight, const uint8_t *src, int width, int height,
                                   size_t srcBytesPerRow) {
    size_t dstBPR = (size_t)width * gBytesPerPixel;
    for (int y = 0; y < height; ++y) {
        memcpy(dstTight + (size_t)y * dstBPR, src + (size_t)y * srcBytesPerRow, dstBPR);
    }
}

NS_INLINE void copyPadOrCropToTight(uint8_t *dstTight, int dstW, int dstH, const uint8_t *src, int srcW, int srcH,
                                    size_t srcBytesPerRow) {
    const int bpp = gBytesPerPixel;
    const size_t dstBPR = (size_t)dstW * (size_t)bpp;
    const int overlapW = srcW < dstW ? srcW : dstW;
    const int overlapH = srcH < dstH ? srcH : dstH;

    // 1) Copy overlap region row-by-row
    if (overlapW > 0 && overlapH > 0) {
        const size_t copyBytes = (size_t)overlapW * (size_t)bpp;
        for (int y = 0; y < overlapH; ++y) {
            uint8_t *drow = dstTight + (size_t)y * dstBPR;
            const uint8_t *srow = src + (size_t)y * srcBytesPerRow;
            memcpy(drow, srow, copyBytes);
            // 2) Right pad by replicating last pixel if needed
            if (dstW > overlapW) {
                const uint8_t *lastPx = (overlapW > 0) ? (drow + ((size_t)overlapW - 1) * (size_t)bpp) : drow;
                for (int x = overlapW; x < dstW; ++x) {
                    memcpy(drow + (size_t)x * (size_t)bpp, lastPx, (size_t)bpp);
                }
            }
        }
    }

    // 3) Bottom pad by replicating last valid row if needed
    if (dstH > overlapH) {
        uint8_t *lastRow = (overlapH > 0) ? (dstTight + (size_t)(overlapH - 1) * dstBPR) : dstTight;
        for (int y = overlapH; y < dstH; ++y) {
            uint8_t *drow = dstTight + (size_t)y * dstBPR;
            memcpy(drow, lastRow, dstBPR);
        }
    }
}
