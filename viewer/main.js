import RFB from '/novnc/core/rfb.js';
import { mountMirror } from './mirror.mjs';

// The native window can request a reconnect without adding browser controls.
window.iPhoneMirror = mountMirror(RFB);
