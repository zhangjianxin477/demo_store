param(
    [Parameter(Mandatory=$true)][string]$VideoPath,
    [Parameter(Mandatory=$true)][double]$Second,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [int]$Width = 1365,
    [int]$Height = 768
)

Add-Type -AssemblyName PresentationCore, PresentationFramework, WindowsBase

$ErrorActionPreference = 'Stop'
$videoUri = [Uri]::new((Resolve-Path -LiteralPath $VideoPath).Path)
$out = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)

$window = [System.Windows.Window]::new()
$window.Width = $Width
$window.Height = $Height
$window.WindowStyle = 'None'
$window.ShowInTaskbar = $false
$window.Left = -20000
$window.Top = -20000
$window.Background = [System.Windows.Media.Brushes]::Black

$media = [System.Windows.Controls.MediaElement]::new()
$media.Width = $Width
$media.Height = $Height
$media.Stretch = [System.Windows.Media.Stretch]::Uniform
$media.LoadedBehavior = [System.Windows.Controls.MediaState]::Manual
$media.UnloadedBehavior = [System.Windows.Controls.MediaState]::Manual
$media.Volume = 0
$media.Source = $videoUri
$window.Content = $media

$script:opened = $false
$script:failed = $false
$media.add_MediaOpened({ $script:opened = $true })
$media.add_MediaFailed({ $script:failed = $true })

$window.Show()
$media.Play()

$deadline = [DateTime]::Now.AddSeconds(12)
while (-not $script:opened -and -not $script:failed -and [DateTime]::Now -lt $deadline) {
    [System.Windows.Threading.Dispatcher]::CurrentDispatcher.Invoke(
        [Action]{},
        [System.Windows.Threading.DispatcherPriority]::Background
    )
    Start-Sleep -Milliseconds 50
}

if ($script:failed -or -not $script:opened) {
    $window.Close()
    throw "MediaElement failed to open video."
}

$media.Position = [TimeSpan]::FromSeconds($Second)
Start-Sleep -Milliseconds 900
$media.Pause()

$window.Measure([System.Windows.Size]::new($Width, $Height))
$window.Arrange([System.Windows.Rect]::new(0, 0, $Width, $Height))
$window.UpdateLayout()

$rtb = [System.Windows.Media.Imaging.RenderTargetBitmap]::new(
    $Width,
    $Height,
    96,
    96,
    [System.Windows.Media.PixelFormats]::Pbgra32
)
$rtb.Render($window)

$encoder = [System.Windows.Media.Imaging.PngBitmapEncoder]::new()
$encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
$stream = [System.IO.File]::Create($out)
try {
    $encoder.Save($stream)
} finally {
    $stream.Close()
    $media.Stop()
    $window.Close()
}

Write-Output $out
