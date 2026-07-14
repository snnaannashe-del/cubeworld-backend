require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

/**
 * Hardhat конфиг для деплоя CUBE Token на Polygon
 *
 * Переменные окружения (.env):
 *   DEPLOYER_PRIVATE_KEY  — приватный ключ кошелька деплоера (с MATIC на балансе)
 *   MINTER_ADDRESS           — адрес бэкенд-сервера CubeWorld (будет минтить награды)
 *   POLYGONSCAN_API_KEY   — для верификации контракта на PolygonScan
 */

const DEPLOYER_KEY = process.env.DEPLOYER_PRIVATE_KEY || "0x" + "0".repeat(64);
const POLYGONSCAN_KEY = process.env.POLYGONSCAN_API_KEY || "";

module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },

  networks: {
    // ── Polygon Amoy (тестнет, бесплатно) ──────────────────
    amoy: {
      url: "https://rpc-amoy.polygon.technology",
      accounts: [DEPLOYER_KEY],
      chainId: 80002,
      gasPrice: 25000000000, // 25 gwei
    },

    // ── Polygon Mainnet ($5 один раз) ──────────────────────
    polygon: {
      url: "https://polygon-rpc.com",
      accounts: [DEPLOYER_KEY],
      chainId: 137,
      gasPrice: 50000000000, // 50 gwei
    },
  },

  etherscan: {
    apiKey: {
      polygon: POLYGONSCAN_KEY,
      polygonAmoy: POLYGONSCAN_KEY,
    },
    customChains: [
      {
        network: "polygonAmoy",
        chainId: 80002,
        urls: {
          apiURL: "https://api-amoy.polygonscan.com/api",
          browserURL: "https://amoy.polygonscan.com",
        },
      },
    ],
  },
};
