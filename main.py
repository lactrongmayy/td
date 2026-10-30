<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TÁO XANH ✦ Profile</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        body {
            background-color: #0b0f12;
            color: #ffffff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }

        .card-container {
            width: 100%;
            max-width: 420px;
            background: rgba(18, 25, 32, 0.85);
            border: 1px solid rgba(74, 222, 128, 0.2);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 0 25px rgba(0, 255, 128, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
        }

        .apple-icon {
            font-size: 40px;
            margin-bottom: 10px;
            filter: drop-shadow(0 0 10px rgba(74, 222, 128, 0.8));
        }

        .title {
            font-size: 28px;
            font-weight: 800;
            letter-spacing: 2px;
            color: #4ade80;
            text-shadow: 0 0 12px rgba(74, 222, 128, 0.6);
            margin-bottom: 6px;
        }

        .subtitle {
            font-size: 12px;
            color: #9ca3af;
            letter-spacing: 1px;
            margin-bottom: 20px;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(74, 222, 128, 0.1);
            border: 1px solid rgba(74, 222, 128, 0.3);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            color: #4ade80;
            margin-bottom: 16px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background-color: #4ade80;
            border-radius: 50%;
            box-shadow: 0 0 8px #4ade80;
        }

        .quote {
            font-style: italic;
            font-size: 13px;
            color: #d1d5db;
            margin: 12px 0;
            line-height: 1.5;
        }

        .user-info {
            font-size: 14px;
            color: #9ca3af;
            margin-bottom: 16px;
        }

        /* Player nhạc */
        .music-player {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            padding: 12px;
            display: flex;
            align-items: center;
            gap: 12px;
            margin: 16px 0;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }

        .cover-art {
            width: 48px;
            height: 48px;
            border-radius: 8px;
            background: #222;
            object-fit: cover;
        }

        .song-details {
            text-align: left;
            flex-grow: 1;
        }

        .song-title {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
        }

        .song-artist {
            font-size: 12px;
            color: #9ca3af;
        }

        .time {
            font-size: 11px;
            color: #6b7280;
        }

        /* Nút Discord/Community */
        .btn-community {
            width: 100%;
            background: #4ade80;
            color: #0b0f12;
            font-weight: 700;
            font-size: 13px;
            padding: 12px;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 10px;
        }

        .btn-community:hover {
            background: #22c55e;
            box-shadow: 0 0 15px rgba(74, 222, 128, 0.4);
        }
    </style>
</head>
<body>

    <div class="card-container">
        <div class="apple-icon">🍏</div>
        <h1 class="title">TÁO XANH</h1>
        <p class="subtitle">CLICK TO ENTER & CHILL</p>

        <div class="status-badge">
            <span class="status-dot"></span> Online ✦ Táo
        </div>

        <p class="quote">“Tình yêu sẽ cháy nhưng không tro tàn . ! .”</p>
        <p class="user-info">📍 72 Đà Nẵng</p>

        <!-- Trình phát nhạc -->
        <div class="music-player">
            <div class="cover-art" style="background-color: #333;"></div>
            <div class="song-details">
                <div class="song-title">GIAYPHUT</div>
                <div class="song-artist">kidsai • Táo Xanh Playlist</div>
            </div>
            <span class="time">01:56</span>
        </div>

        <p class="quote">
            “Vì cho dù là còn Thương, hay đã quên<br>
            Chỉ cần còn Tình Yêu, là ta sẽ đến được Thiên Đường<br>
            Mình quấn quýt vào nhau, thật lâu<br>
            Rồi sẽ lại đến lúc em Gật đầu<br>
            Chìm Soul . ! .”
        </p>

        <button class="btn-community" onclick="copyLink()">
            72 ĐÀ NẴNG ✦ TÁO XANH × DISCORD COMMUNITY
        </button>
    </div>

    <script>
        function copyLink() {
            navigator.clipboard.writeText(window.location.href);
            alert("Đã sao chép liên kết vào bộ nhớ tạm! ✦");
        }
    </script>
</body>
</html>