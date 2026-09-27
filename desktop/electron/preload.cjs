const { contextBridge, ipcRenderer } = require('electron')

// renderer 只得到选择目录这一项能力，不暴露通用 IPC 或文件系统访问。
if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('xuanyueDesktop', {
    chooseProjectFolder: () => ipcRenderer.invoke('xuanyue:choose-project-folder'),
  })
}
