using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;

// Compiled as winexe so Task Scheduler never allocates a console for this parent.
// Unlike the n8n launcher, do NOT use a kill-on-close job: recovery may start
// durable business services which must outlive this one-shot health check.
internal static class NoConsoleLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 2) return 64;
            foreach (string value in args)
            {
                if (!Path.IsPathRooted(value) || value.IndexOfAny(new[] { '"', '\r', '\n' }) >= 0 ||
                    !File.Exists(value)) return 64;
            }
            if (!String.Equals(Path.GetFileName(args[0]), "pwsh.exe", StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(Path.GetExtension(args[1]), ".ps1", StringComparison.OrdinalIgnoreCase)) return 64;
            var start = new ProcessStartInfo
            {
                FileName = Path.GetFullPath(args[0]),
                Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File \"" +
                    Path.GetFullPath(args[1]) + "\" -Action Check -Execute",
                WorkingDirectory = Path.GetDirectoryName(Path.GetFullPath(args[1])),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using (var child = Process.Start(start))
            {
                if (child == null) return 70;
                child.StandardInput.Close();
                // Drain without buffering/logging any possible business data or credentials.
                Task output = child.StandardOutput.BaseStream.CopyToAsync(Stream.Null);
                Task error = child.StandardError.BaseStream.CopyToAsync(Stream.Null);
                child.WaitForExit();
                // Descendants may inherit a redirected handle. Their lifetime must not keep
                // the scheduler task alive after the direct health-check process has exited.
                Task.WaitAll(new[] { output, error }, 1000);
                return child.ExitCode;
            }
        }
        catch { return 70; } // No GUI error dialog; failures reach Task Scheduler as exit codes.
    }
}
