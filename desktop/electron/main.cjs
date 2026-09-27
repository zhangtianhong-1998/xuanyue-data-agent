const { app, BrowserWindow } = require('electron')

// 此窗口只加载本机 Python 服务提供的构建产物；没有 Node 桥接或外链导航。
const APP_URL = 'http://127.0.0.1:8787/'

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 720,
    minHeight: 620,
    backgroundColor: '#ffffff',
    title: '玄月 · Data Agent',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
    },
  })

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => {
    if (url !== APP_URL) event.preventDefault()
  })
  window.webContents.on('will-redirect', (event) => event.preventDefault())
  void window.loadURL(APP_URL)
}

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
