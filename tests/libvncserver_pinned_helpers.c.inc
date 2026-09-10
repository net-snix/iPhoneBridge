/* Unchanged source excerpts from LibVNCServer commit
 * 42494999e6492aaab9c1db785ecd293ef10b3aed (0.9.15+git).
 * rfbserver.c: rfbIncrClientRef, rfbDecrClientRef, and the exact unlink/ref-wait
 * prefix of rfbClientConnectionGone. Copyright (C) 2011-2012 D. R. Commander;
 * 2005 Rohit Kumar, Johannes E. Schindelin; 2002 RealVNC Ltd.; 2001 Dan McGuirk;
 * 1999 AT&T Laboratories Cambridge. GPL version 2 or later.
 * main.c: rfbInitServerFormat. Copyright (C) 2001 Johannes E. Schindelin;
 * 2001 Dan McGuirk; 1999 AT&T Laboratories Cambridge. See upstream GPL license.
 * Source: https://github.com/LibVNC/libvncserver/tree/42494999e6492aaab9c1db785ecd293ef10b3aed
 *
 * This fixture supplies only unchanged dependencies. Modified snapshot and
 * resize implementations must be extracted from the reviewed canonical patch.
 * The unlink excerpt deliberately ends before unrelated resource cleanup;
 * the harness appends checked mutex destruction and actual client deallocation.
 */
/* BEGIN REFS */
void rfbIncrClientRef(rfbClientPtr cl)
{
  LOCK(cl->refCountMutex);
  cl->refCount++;
  UNLOCK(cl->refCountMutex);
}

void rfbDecrClientRef(rfbClientPtr cl)
{
  LOCK(cl->refCountMutex);
  cl->refCount--;
  if(cl->refCount<=0) /* just to be sure also < 0 */
    TSIGNAL(cl->deleteCond);
  UNLOCK(cl->refCountMutex);
}
/* END REFS */

/* BEGIN FORMAT */
static void rfbInitServerFormat(rfbScreenInfoPtr screen, int bitsPerSample)
{
   rfbPixelFormat* format=&screen->serverFormat;

   format->bitsPerPixel = screen->bitsPerPixel;
   format->depth = screen->depth;
   format->bigEndian = rfbEndianTest?FALSE:TRUE;
   format->trueColour = TRUE;
   screen->colourMap.count = 0;
   screen->colourMap.is16 = 0;
   screen->colourMap.data.bytes = NULL;

   if (format->bitsPerPixel == 8) {
     format->redMax = 7;
     format->greenMax = 7;
     format->blueMax = 3;
     format->redShift = 0;
     format->greenShift = 3;
     format->blueShift = 6;
   } else {
     format->redMax = (1 << bitsPerSample) - 1;
     format->greenMax = (1 << bitsPerSample) - 1;
     format->blueMax = (1 << bitsPerSample) - 1;
     if(rfbEndianTest) {
       format->redShift = 0;
       format->greenShift = bitsPerSample;
       format->blueShift = bitsPerSample * 2;
     } else {
       if(format->bitsPerPixel==8*3) {
	 format->redShift = bitsPerSample*2;
	 format->greenShift = bitsPerSample*1;
	 format->blueShift = 0;
       } else {
	 format->redShift = bitsPerSample*3;
	 format->greenShift = bitsPerSample*2;
	 format->blueShift = bitsPerSample;
       }
     }
   }
}
/* END FORMAT */

/* BEGIN UNLINK_AND_WAIT */
void
rfbClientConnectionGone(rfbClientPtr cl)
{
#if defined(LIBVNCSERVER_HAVE_LIBZ) && defined(LIBVNCSERVER_HAVE_LIBJPEG)
    int i;
#endif

    LOCK(rfbClientListMutex);

    if (cl->prev)
        cl->prev->next = cl->next;
    else
        cl->screen->clientHead = cl->next;
    if (cl->next)
        cl->next->prev = cl->prev;

    UNLOCK(rfbClientListMutex);

#if defined(LIBVNCSERVER_HAVE_LIBPTHREAD) || defined(LIBVNCSERVER_HAVE_WIN32THREADS)
    if (cl->screen->backgroundLoop) {
      int i;
      do {
	LOCK(cl->refCountMutex);
	i=cl->refCount;
	if(i>0)
	  WAIT(cl->deleteCond,cl->refCountMutex);
	UNLOCK(cl->refCountMutex);
      } while(i>0);
    }
#endif


/* END UNLINK_AND_WAIT */
