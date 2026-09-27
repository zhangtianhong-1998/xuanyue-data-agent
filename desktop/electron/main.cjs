const path = require('node:path')
const { app, BrowserWindow, dialog, ipcMain } = require('electron')

// 此窗口只加载本机 Python 服务提供的构建产物；原生能力经单用途桥接调用。
const APP_URL = 'http://127.0.0.1:8787/'
const CHOOSE_PROJECT_FOLDER = 'xuanyue:choose-project-folder'
let mainWindow = null

function isTrustedFrame(event) {
  // IPC 只能来自当前产品窗口的主 frame，不能让子 frame 或其他窗口打开系统选择器。
  const window = mainWindow
  return Boolean(
    window && !window.isDestroyed()
    && event.sender === window.webContents
    && event.senderFrame
    && event.senderFrame === window.webContents.mainFrame
    && event.senderFrame.url === APP_URL,
  )
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 720,
    minHeight: 620,
    backgroundColor: '#ffffff',
    title: '玄月 · Data Agent',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
    },
  })
  mainWindow = window

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== APP_URL) event.preventDefault()
  })
  window.webContents.on('will-redirect', (event) => event.preventDefault())
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null
  })
  void window.loadURL(APP_URL)
}

app.whenReady().then(() => {
  ipcMain.handle(CHOOSE_PROJECT_FOLDER, async (event) => {
    if (!isTrustedFrame(event)) throw new Error('folder_picker_unavailable')
    try {
      const result = await dialog.showOpenDialog(mainWindow, {
        title: '选择项目工作文件夹',
        properties: ['openDirectory'],
      })
      // 系统对话框打开期间窗口可能关闭；旧 frame 不能取得选择结果。
      if (!isTrustedFrame(event)) throw new Error('folder_picker_unavailable')
      return result.canceled ? null : (result.filePaths[0] ?? null)
    } catch {
      // 不把系统路径或 Electron 的原始错误带到页面。
      throw new Error('folder_picker_unavailable')
    }
  })
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
