// DXW Sync Shell Extension
// Windows 文件管理器同步状态覆盖图标
using System;
using System.IO;
using System.Data;
using System.Data.SQLite;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace DxwSyncOverlay
{
    // ========== 覆盖图标状态枚举 ==========
    enum SyncStatus
    {
        Unknown = 0,
        Synced = 1,    // 已同步（绿色对勾）
        Syncing = 2,   // 同步中（蓝色循环）
        Error = 3,     // 同步出错（红色叉）
        Conflict = 4,  // 冲突（黄色感叹号）
    }

    // ========== COM 接口定义 ==========
    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("000214f9-0000-0000-c000-000000000046")]
    interface IShellIconOverlayIdentifier
    {
        [PreserveSig]
        int IsMemberOf([MarshalAs(UnmanagedType.LPWStr)] string pwszPath, uint dwAttrib);

        [PreserveSig]
        int GetOverlayInfo(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pwszIconFile,
            int cchMax,
            out int pIndex,
            out uint pdwFlags);

        [PreserveSig]
        int GetPriority(out int pPriority);
    }

    // ========== 状态数据库读取 ==========
    static class SyncStateDB
    {
        // 数据库路径：~/.dxw_sync_state.db
        static string GetDbPath()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(home, ".dxw_sync_state.db");
        }

        // 同步客户端配置中记录的本地同步文件夹
        static string GetLocalSyncPath()
        {
            try
            {
                string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                string configPath = Path.Combine(home, ".dxw_sync_config.json");
                if (!File.Exists(configPath)) return "";
                string json = File.ReadAllText(configPath);
                // 简单解析 local_path
                int idx = json.IndexOf("\"local_path\"");
                if (idx < 0) return "";
                idx = json.IndexOf(":", idx) + 1;
                int start = json.IndexOf("\"", idx) + 1;
                int end = json.IndexOf("\"", start);
                if (start <= 0 || end <= start) return "";
                return json.Substring(start, end - start).Replace("\\\\", "\\");
            }
            catch { return ""; }
        }

        // 根据完整路径查询同步状态
        public static SyncStatus GetStatus(string fullPath)
        {
            try
            {
                string syncRoot = GetLocalSyncPath();
                if (string.IsNullOrEmpty(syncRoot)) return SyncStatus.Unknown;

                // 规范化路径
                fullPath = fullPath.Replace("/", "\\");
                syncRoot = syncRoot.TrimEnd('\\') + "\\";

                // 只处理同步文件夹内的文件
                if (!fullPath.StartsWith(syncRoot, StringComparison.OrdinalIgnoreCase))
                    return SyncStatus.Unknown;

                // 获取相对路径
                string relPath = fullPath.Substring(syncRoot.Length).Replace("\\", "/");

                string dbPath = GetDbPath();
                if (!File.Exists(dbPath)) return SyncStatus.Unknown;

                using (var conn = new SQLiteConnection($"Data Source={dbPath};Version=3;Read Only=True;"))
                {
                    conn.Open();
                    using (var cmd = conn.CreateCommand())
                    {
                        cmd.CommandText = "SELECT status, local_mtime, server_mtime FROM file_state WHERE relative_path = @path";
                        cmd.Parameters.AddWithValue("@path", relPath);

                        using (var reader = cmd.ExecuteReader())
                        {
                            if (reader.Read())
                            {
                                string status = reader.GetString(0);
                                double localMtime = reader.GetDouble(1);
                                double serverMtime = reader.GetDouble(2);

                                switch (status)
                                {
                                    case "synced":
                                        // 检查本地文件是否比数据库更新（有新改动）
                                        if (File.Exists(fullPath))
                                        {
                                            double fileMtime = File.GetLastWriteTime(fullPath).Subtract(
                                                new DateTime(1970, 1, 1)).TotalSeconds;
                                            if (fileMtime > localMtime + 2)
                                                return SyncStatus.Syncing; // 本地有新改动，等待同步
                                        }
                                        return SyncStatus.Synced;
                                    case "uploading":
                                    case "downloading":
                                        return SyncStatus.Syncing;
                                    case "conflict":
                                        return SyncStatus.Conflict;
                                    case "error":
                                        return SyncStatus.Error;
                                    default:
                                        return SyncStatus.Syncing;
                                }
                            }
                        }
                    }
                }

                // 不在数据库中 → 未同步（显示为同步中，等待上传）
                return SyncStatus.Syncing;
            }
            catch
            {
                return SyncStatus.Unknown;
            }
        }
    }

    // ========== Shell Extension 主类 ==========
    [ComVisible(true)]
    [Guid("A1B2C3D4-E5F6-7890-ABCD-EF1234567890")]
    [ClassInterface(ClassInterfaceType.None)]
    public class SyncOverlayHandler : IShellIconOverlayIdentifier
    {
        // 图标文件路径
        static string GetIconPath(string iconFile)
        {
            string dir = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);
            return Path.Combine(dir, iconFile);
        }

        public int IsMemberOf([MarshalAs(UnmanagedType.LPWStr)] string pwszPath, uint dwAttrib)
        {
            try
            {
                var status = SyncStateDB.GetStatus(pwszPath);
                return (status != SyncStatus.Unknown) ? 0 : 1; // S_OK = 0 表示是成员
            }
            catch
            {
                return 1; // S_FALSE = 1 表示不是成员
            }
        }

        public int GetOverlayInfo(
            [MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pwszIconFile,
            int cchMax,
            out int pIndex,
            out uint pdwFlags)
        {
            // 根据状态返回不同图标
            // 这里简化处理：只用一个图标文件，通过 index 区分
            // 实际可以用多个 .ico 文件
            string iconPath = GetIconPath("sync_icons.dll");
            if (!File.Exists(iconPath))
                iconPath = GetIconPath("sync_synced.ico");

            pwszIconFile.Clear();
            pwszIconFile.Append(iconPath);
            pIndex = 0;
            pdwFlags = 1; // ISIOI_ICONFILE
            return 0;
        }

        public int GetPriority(out int pPriority)
        {
            pPriority = 0;
            return 0;
        }
    }

    // ========== 注册/注销 ==========
    [ComVisible(true)]
    [Guid("B2C3D4E5-F6A7-8901-BCDE-F12345678901")]
    public class SyncOverlayRegistrar
    {
        const string CLSID = "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}";
        const string HandlerName = "DXW Sync Overlay";

        [ComRegisterFunction]
        static void Register(Type t)
        {
            try
            {
                string iconDir = Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location);

                // 注册覆盖图标处理器
                using (var key = Registry.ClassesRoot.CreateSubKey(
                    $@"CLSID\{CLSID}\ShellEx\{{8895b1c6-b41f-4c1c-a562-0d564250836f}}"))
                {
                    key.SetValue("", "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}");
                }

                // 注册 COM 服务器
                using (var key = Registry.ClassesRoot.CreateSubKey($@"CLSID\{CLSID}"))
                {
                    key.SetValue("", HandlerName);
                    key.SetValue(" DisplayName", "DXW Sync Status");
                }

                using (var key = Registry.ClassesRoot.CreateSubKey($@"CLSID\{CLSID}\InprocServer32"))
                {
                    key.SetValue("", "mscoree.dll");
                    key.SetValue("ThreadingModel", "Both");
                    key.SetValue("Class", t.FullName);
                    key.SetValue("Assembly", t.Assembly.FullName);
                    key.SetValue("RuntimeVersion", t.Assembly.ImageRuntimeVersion);
                    key.SetValue("CodeBase", t.Assembly.Location);
                }

                // 刷新图标缓存
                SHChangeNotify(0x08000000, 0x2000, IntPtr.Zero, IntPtr.Zero);

                Console.WriteLine("DXW Sync Overlay 注册成功！");
            }
            catch (Exception ex)
            {
                Console.WriteLine("注册失败: " + ex.Message);
            }
        }

        [ComUnregisterFunction]
        static void Unregister(Type t)
        {
            try
            {
                Registry.ClassesRoot.DeleteSubKeyTree($@"CLSID\{CLSID}", false);
                SHChangeNotify(0x08000000, 0x2000, IntPtr.Zero, IntPtr.Zero);
                Console.WriteLine("DXW Sync Overlay 注销成功！");
            }
            catch (Exception ex)
            {
                Console.WriteLine("注销失败: " + ex.Message);
            }
        }

        [System.Runtime.InteropServices.DllImport("shell32.dll")]
        static extern void SHChangeNotify(int eventId, uint flags, IntPtr item1, IntPtr item2);
    }
}
