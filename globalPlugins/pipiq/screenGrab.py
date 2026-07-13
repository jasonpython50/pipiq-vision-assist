# Screen and clipboard image capture via Win32 GDI, plus a pure-Python PNG
# encoder (zlib + struct), so no binary dependencies need to be bundled.
# Deliberately NVDA-free so it can be unit-tested outside NVDA.

import ctypes
import ctypes.wintypes as wintypes
import os
import struct
import time
import zlib

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# Handle- and pointer-returning functions must be fully declared, or ctypes
# truncates values to 32 bits and crashes on 64-bit Python (NVDA 2026.1+).
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.SetBrushOrgEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.StretchBlt.argtypes = [
	ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
	ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
	wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
	ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
	ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalFree.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.SHGetFolderPathW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR]

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000  # include layered windows in the capture
HALFTONE = 4
DIB_RGB_COLORS = 0
BI_RGB = 0
BI_BITFIELDS = 3
CF_DIB = 8
CF_HDROP = 15
CF_DIBV5 = 17
GA_ROOT = 2
GMEM_MOVEABLE = 0x0002
CSIDL_MYPICTURES = 39
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITOR_DEFAULTTONEAREST = 2


class BITMAPINFOHEADER(ctypes.Structure):
	_fields_ = [
		("biSize", wintypes.DWORD),
		("biWidth", wintypes.LONG),
		("biHeight", wintypes.LONG),
		("biPlanes", wintypes.WORD),
		("biBitCount", wintypes.WORD),
		("biCompression", wintypes.DWORD),
		("biSizeImage", wintypes.DWORD),
		("biXPelsPerMeter", wintypes.LONG),
		("biYPelsPerMeter", wintypes.LONG),
		("biClrUsed", wintypes.DWORD),
		("biClrImportant", wintypes.DWORD),
	]


class CaptureError(Exception):
	pass


def getVirtualScreenRect():
	"""(left, top, width, height) of the full desktop across all monitors, physical pixels."""
	return (
		user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
		user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
		user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
		user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
	)


def intersectWithVirtualScreen(left, top, width, height):
	"""Clamp a rectangle to the visible desktop; returns None if nothing is visible."""
	vl, vt, vw, vh = getVirtualScreenRect()
	l = max(left, vl)
	t = max(top, vt)
	r = min(left + width, vl + vw)
	b = min(top + height, vt + vh)
	if r - l < 4 or b - t < 4:
		return None
	return (l, t, r - l, b - t)


def getMonitors():
	"""List of monitor rects [(left, top, width, height, isPrimary)], primary first."""
	monitors = []
	MonitorEnumProc = ctypes.WINFUNCTYPE(
		wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
		ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
	)

	def cb(hMon, hdc, lprc, lparam):
		rc = lprc.contents
		isPrimary = rc.left == 0 and rc.top == 0
		monitors.append((rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top, isPrimary))
		return True

	user32.EnumDisplayMonitors(0, None, MonitorEnumProc(cb), 0)
	monitors.sort(key=lambda m: not m[4])
	return monitors


def monitorIndexForPoint(x, y):
	"""(1-based index, total count) of the monitor containing the point; primary is 1."""
	monitors = getMonitors()
	for i, (ml, mt, mw, mh, _p) in enumerate(monitors):
		if ml <= x < ml + mw and mt <= y < mt + mh:
			return i + 1, len(monitors)
	return 1, max(1, len(monitors))


def _encodePng(rgb, width, height):
	stride = width * 3
	out = bytearray((stride + 1) * height)
	for y in range(height):
		row = y * (stride + 1)
		out[row] = 0  # filter type: none
		out[row + 1:row + 1 + stride] = rgb[y * stride:(y + 1) * stride]
	compressed = zlib.compress(bytes(out), 6)

	def chunk(tag, data):
		return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

	ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
	return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def _bgraToRgb(raw, width, height):
	n = width * height
	rgb = bytearray(n * 3)
	# Sliced assignment runs at C speed; raw is top-down BGRA with no padding.
	rgb[0::3] = raw[2::4]
	rgb[1::3] = raw[1::4]
	rgb[2::3] = raw[0::4]
	return rgb


def captureRgb(left, top, width, height, maxDim=None):
	"""Capture a physical-pixel screen rectangle and return (rgb, outW, outH, isBlack).

	rgb is a top-down, unpadded RGB bytearray. When maxDim is given, the capture
	is downscaled on the GDI side so the longest edge is at most maxDim.

	isBlack is True when the capture is essentially all black — e.g. NVDA's
	screen curtain is on (detected this way because the curtain implementation
	moved between NVDA versions) or the content is DRM protected.
	"""
	if width < 4 or height < 4:
		raise CaptureError("capture rectangle too small")
	scale = min(1.0, float(maxDim) / max(width, height)) if maxDim else 1.0
	dw = max(1, int(width * scale))
	dh = max(1, int(height * scale))

	hScreen = user32.GetDC(0)
	if not hScreen:
		raise CaptureError("GetDC failed")
	hDC = gdi32.CreateCompatibleDC(hScreen)
	hBmp = gdi32.CreateCompatibleBitmap(hScreen, dw, dh)
	try:
		if not hDC or not hBmp:
			raise CaptureError("could not create capture bitmap")
		old = gdi32.SelectObject(hDC, hBmp)
		gdi32.SetStretchBltMode(hDC, HALFTONE)
		gdi32.SetBrushOrgEx(hDC, 0, 0, None)
		ok = gdi32.StretchBlt(
			hDC, 0, 0, dw, dh,
			hScreen, left, top, width, height,
			SRCCOPY | CAPTUREBLT,
		)
		gdi32.SelectObject(hDC, old)
		if not ok:
			raise CaptureError("StretchBlt failed")
		bmi = BITMAPINFOHEADER()
		bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
		bmi.biWidth = dw
		bmi.biHeight = -dh  # negative = top-down rows
		bmi.biPlanes = 1
		bmi.biBitCount = 32
		bmi.biCompression = BI_RGB
		buf = ctypes.create_string_buffer(dw * dh * 4)
		lines = gdi32.GetDIBits(hDC, hBmp, 0, dh, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
		if lines != dh:
			raise CaptureError("GetDIBits failed")
	finally:
		if hBmp:
			gdi32.DeleteObject(hBmp)
		if hDC:
			gdi32.DeleteDC(hDC)
		user32.ReleaseDC(0, hScreen)
	rgb = _bgraToRgb(buf.raw, dw, dh)
	sample = rgb[::997]
	isBlack = max(sample) < 8 if sample else True
	return rgb, dw, dh, isBlack


def captureRect(left, top, width, height, maxDim=1568):
	"""Like captureRgb, but returns (pngBytes, outW, outH, isBlack)."""
	rgb, dw, dh, isBlack = captureRgb(left, top, width, height, maxDim)
	return _encodePng(rgb, dw, dh), dw, dh, isBlack


def encodePng(rgb, width, height):
	"""Encode a top-down, unpadded RGB buffer as PNG bytes."""
	return _encodePng(rgb, width, height)


def screenshotDirectory():
	"""The folder screenshots are saved to: <Pictures>\\PiPiQ Screenshots.

	Uses the shell's notion of Pictures (correct even when OneDrive has moved
	it), falling back to the user profile if the shell call fails.
	"""
	buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
	if shell32.SHGetFolderPathW(None, CSIDL_MYPICTURES, None, 0, buf) == 0 and buf.value:
		pictures = buf.value
	else:
		pictures = os.path.join(os.path.expanduser("~"), "Pictures")
	return os.path.join(pictures, "PiPiQ Screenshots")


def saveScreenshot(rgb, width, height, directory=None):
	"""Write the RGB buffer to a timestamped PNG file; returns the full path."""
	directory = directory or screenshotDirectory()
	os.makedirs(directory, exist_ok=True)
	base = time.strftime("pipiq-%Y-%m-%d-%H-%M-%S")
	path = os.path.join(directory, base + ".png")
	counter = 1
	while os.path.exists(path):
		counter += 1
		path = os.path.join(directory, "%s-%d.png" % (base, counter))
	with open(path, "wb") as f:
		f.write(_encodePng(rgb, width, height))
	return path


def _rgbToDib(rgb, width, height):
	"""Pack a top-down RGB buffer into a 32-bit bottom-up DIB (CF_DIB layout)."""
	header = struct.pack(
		"<IiiHHIIiiII",
		40, width, height,  # positive height = bottom-up rows
		1, 32, BI_RGB,
		width * height * 4,
		0, 0, 0, 0,
	)
	pixels = bytearray(width * height * 4)
	srcStride = width * 3
	dstStride = width * 4
	for y in range(height):
		row = rgb[y * srcStride:(y + 1) * srcStride]
		dst = (height - 1 - y) * dstStride
		pixels[dst + 0:dst + dstStride:4] = row[2::3]  # blue
		pixels[dst + 1:dst + dstStride:4] = row[1::3]  # green
		pixels[dst + 2:dst + dstStride:4] = row[0::3]  # red
	return header + bytes(pixels)


def copyImageToClipboard(rgb, width, height):
	"""Place the RGB buffer on the clipboard as a bitmap (CF_DIB)."""
	dib = _rgbToDib(rgb, width, height)
	if not _openClipboard():
		raise CaptureError("clipboard busy")
	try:
		user32.EmptyClipboard()
		handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
		if not handle:
			raise CaptureError("GlobalAlloc failed")
		ptr = kernel32.GlobalLock(handle)
		if not ptr:
			kernel32.GlobalFree(handle)
			raise CaptureError("GlobalLock failed")
		ctypes.memmove(ptr, dib, len(dib))
		kernel32.GlobalUnlock(handle)
		if not user32.SetClipboardData(CF_DIB, handle):
			# Ownership only transfers on success; free it ourselves on failure.
			kernel32.GlobalFree(handle)
			raise CaptureError("SetClipboardData failed")
	finally:
		user32.CloseClipboard()


def rootWindow(hwnd):
	"""Top-level ancestor of a window handle, or None."""
	if not hwnd:
		return None
	return user32.GetAncestor(hwnd, GA_ROOT)


def topLevelWindowAt(x, y):
	"""(top-level hwnd, window title) of whatever is visible at a screen point."""
	hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
	root = rootWindow(hwnd)
	if not root:
		return None, ""
	buf = ctypes.create_unicode_buffer(256)
	user32.GetWindowTextW(root, buf, 256)
	return root, buf.value


def occlusionReport(left, top, width, height, ownerHwnd):
	"""Check whether the given screen rect is actually showing ownerHwnd's window.

	Samples the center plus the four quarter points. Returns
	(blockedCount, totalCount, coveringTitle). blockedCount is 0 when nothing
	overlaps; coveringTitle is the title of the most frequent covering window.
	"""
	ownerRoot = rootWindow(ownerHwnd)
	if not ownerRoot:
		return 0, 0, ""
	points = (
		(left + width // 2, top + height // 2),
		(left + width // 4, top + height // 4),
		(left + 3 * width // 4, top + height // 4),
		(left + width // 4, top + 3 * height // 4),
		(left + 3 * width // 4, top + 3 * height // 4),
	)
	blocked = 0
	titles = {}
	for x, y in points:
		root, title = topLevelWindowAt(x, y)
		if root and root != ownerRoot:
			blocked += 1
			titles[title] = titles.get(title, 0) + 1
	covering = max(titles, key=titles.get) if titles else ""
	return blocked, len(points), covering


# ---------------------------------------------------------------------------
# Clipboard image extraction

_MIME_BY_EXT = {
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".gif": "image/gif",
	".webp": "image/webp",
}
MAX_FILE_BYTES = 8 * 1024 * 1024


def _dibToPng(dib, maxDim=1568):
	"""Convert a packed DIB (CF_DIB clipboard data or BMP file body) to PNG bytes."""
	if len(dib) < 40:
		return None
	(biSize, biWidth, biHeight, _planes, biBitCount, biCompression) = struct.unpack_from("<IiiHHI", dib, 0)
	if biSize < 40 or biWidth <= 0 or biHeight == 0:
		return None
	if biBitCount not in (24, 32):
		return None
	if biCompression not in (BI_RGB, BI_BITFIELDS):
		return None
	height = abs(biHeight)
	bottomUp = biHeight > 0
	offset = biSize
	if biCompression == BI_BITFIELDS and biSize == 40:
		offset += 12  # three DWORD channel masks follow a plain BITMAPINFOHEADER
	clrUsed = struct.unpack_from("<I", dib, 32)[0]
	offset += clrUsed * 4
	bpp = biBitCount // 8
	stride = (biWidth * bpp + 3) & ~3
	if offset + stride * height > len(dib):
		return None

	# Nearest-neighbour downscale indices keep pure-Python cost bounded.
	scale = min(1.0, float(maxDim) / max(biWidth, height))
	dw = max(1, int(biWidth * scale))
	dh = max(1, int(height * scale))
	xIdx = [int(x * biWidth / dw) * bpp for x in range(dw)]
	rgb = bytearray(dw * dh * 3)
	pos = 0
	for y in range(dh):
		srcY = int(y * height / dh)
		if bottomUp:
			srcY = height - 1 - srcY
		base = offset + srcY * stride
		for xoff in xIdx:
			p = base + xoff
			rgb[pos] = dib[p + 2]
			rgb[pos + 1] = dib[p + 1]
			rgb[pos + 2] = dib[p]
			pos += 3
	return _encodePng(rgb, dw, dh)


def _openClipboard(retries=6):
	for i in range(retries):
		if user32.OpenClipboard(0):
			return True
		time.sleep(0.05)
	return False


def _clipboardFilePath():
	hDrop = user32.GetClipboardData(CF_HDROP)
	if not hDrop:
		return None
	buf = ctypes.create_unicode_buffer(1024)
	if shell32.DragQueryFileW(hDrop, 0, buf, 1024):
		return buf.value
	return None


def _clipboardDibBytes():
	handle = user32.GetClipboardData(CF_DIB)
	if not handle:
		return None
	ptr = kernel32.GlobalLock(handle)
	if not ptr:
		return None
	try:
		size = kernel32.GlobalSize(handle)
		return ctypes.string_at(ptr, size)
	finally:
		kernel32.GlobalUnlock(handle)


def getClipboardImage(maxDim=1568):
	"""Return (imageBytes, mime) for an image on the clipboard.

	Handles bitmap data (e.g. copied from a snip, browser, or WhatsApp) and a
	copied image *file* (Explorer copy). Returns None when the clipboard holds
	no image; raises CaptureError for images we cannot convert.
	"""
	if not _openClipboard():
		raise CaptureError("clipboard busy")
	try:
		if user32.IsClipboardFormatAvailable(CF_DIB) or user32.IsClipboardFormatAvailable(CF_DIBV5):
			dib = _clipboardDibBytes()
			if dib:
				png = _dibToPng(dib, maxDim)
				if png:
					return png, "image/png"
				raise CaptureError("unsupported bitmap format")
		if user32.IsClipboardFormatAvailable(CF_HDROP):
			path = _clipboardFilePath()
			if path:
				ext = os.path.splitext(path)[1].lower()
				if ext == ".bmp":
					with open(path, "rb") as f:
						data = f.read()
					# A BMP file is a 14-byte BITMAPFILEHEADER followed by a packed DIB.
					png = _dibToPng(data[14:], maxDim) if data[:2] == b"BM" else None
					if png:
						return png, "image/png"
					raise CaptureError("unsupported bitmap file")
				mime = _MIME_BY_EXT.get(ext)
				if mime:
					if os.path.getsize(path) > MAX_FILE_BYTES:
						raise CaptureError("image file too large")
					with open(path, "rb") as f:
						return f.read(), mime
		return None
	finally:
		user32.CloseClipboard()
