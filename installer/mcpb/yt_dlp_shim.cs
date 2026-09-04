using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class YtDlpShim
{
    public static int Main(string[] args)
    {
        var server = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "cueprecise-mcp.exe");
        var start = new ProcessStartInfo(server) { UseShellExecute = false };
        start.Arguments = "--yt-dlp " + JoinArguments(args);
        using (var child = Process.Start(start))
        {
            if (child == null) return 1;
            child.WaitForExit();
            return child.ExitCode;
        }
    }

    private static string JoinArguments(string[] args)
    {
        var quoted = new string[args.Length];
        for (var i = 0; i < args.Length; i++)
            quoted[i] = Quote(args[i]);
        return string.Join(" ", quoted);
    }

    private static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
            return value;
        var result = new StringBuilder("\"");
        var slashes = 0;
        foreach (var character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(character);
            slashes = 0;
        }
        result.Append('\\', slashes * 2).Append('"');
        return result.ToString();
    }
}
