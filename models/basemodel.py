
from models.volatility.sports import TennisVModel, SoccerVModel, BasketballVModel, HockeyVModel, AmericanFootballVModel
from models.volatility.weather import TemperatureVModel
from models.volatility.economics import InflationVModel, UnemploymentVModel, GDPVModel
from models.volatility.crypto import BTCVModel, ETHVModel, SOLVModel, XRPVModel, DOGEVModel, BNBVModel
from models.fairprice.sports import TennisFModel, SoccerFModel, BasketballFModel, HockeyFModel, AmericanFootballFModel
from models.fairprice.weather import TemperatureFModel
from models.fairprice.economics import InflationFModel, UnemploymentFModel, GDPFModel
from models.fairprice.crypto import BTCFModel, ETHFModel, SOLFModel, XRPFModel, DOGEFModel, BNBFModel
from models.volatility.financialmarkets import SP500VModel, NASDAQVModel, DOWJVModel
from models.fairprice.financialmarkets import SP500FModel, NASDAQFModel, DOWJFModel

class Model:

    def __init__(self):

        self.volatility_models = {
            'tennis': TennisVModel(),
            'soccer': SoccerVModel(),
            'basketball': BasketballVModel(),
            'hockey': HockeyVModel(),
            'american_football': AmericanFootballVModel(),
            'temperature': TemperatureVModel(),
            'inflation': InflationVModel(),
            'unemployment': UnemploymentVModel(),
            'gdp': GDPVModel(),
            'btc': BTCVModel(),
            'eth': ETHVModel(),
            'sol': SOLVModel(),
            'xrp': XRPVModel(),
            'doge': DOGEVModel(),
            'bnb': BNBVModel(),
            'sp500': SP500VModel(),
            'nasdaq': NASDAQVModel(),
            'dowj': DOWJVModel()
        }

        self.fairprice_models = {
            'tennis': TennisFModel(),
            'soccer': SoccerFModel(),
            'basketball': BasketballFModel(),
            'hockey': HockeyFModel(),
            'american_football': AmericanFootballFModel(),
            'temperature': TemperatureFModel(),
            'inflation': InflationFModel(),
            'unemployment': UnemploymentFModel(),
            'gdp': GDPFModel(),
            'btc': BTCFModel(),
            'eth': ETHFModel(),
            'sol': SOLFModel(),
            'xrp': XRPFModel(),
            'doge': DOGEFModel(),
            'bnb': BNBFModel(),
            'sp500': SP500FModel(),
            'nasdaq': NASDAQFModel(),
            'dowj': DOWJFModel()
        }

    def calculate_average_volatility(self, market):
        """Fail-closed: categoría desconocida ('unknown', asignada por
        outer_apis.kalshi._infer_category cuando no reconoce el ticker) devuelve
        volatilidad 0.0 en vez de reventar o inventar un valor — nunca debe
        traducirse en aprobar una oportunidad de arb por falta de dato."""
        market_type = market.get('type')
        volatility_model = self.volatility_models.get(market_type)
        if volatility_model is None:
            return 0.0
        return volatility_model.calculate_volatility(market)

    def calculate_fair_price(self, market):
        """Fail-closed: sin modelo para la categoría, el fair price colapsa
        al precio de mercado actual (edge = 0), nunca se inventa un edge
        fantasma por ausencia de modelo."""
        market_type = market.get('type')
        fairprice_model = self.fairprice_models.get(market_type)
        if fairprice_model is None:
            return {
                'fair_a_price': market.get('current_a_price'),
                'fair_b_price': market.get('current_b_price'),
            }
        return fairprice_model.calculate_fair_price(market)