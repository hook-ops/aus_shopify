from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import time
import re
import os

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variable to store scraped data
scraped_data = {}


# When user click scraped product's image, user can change product image
UPLOAD_FOLDER = 'static/uploads/'  # Folder where uploaded images are saved
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/upload-image', methods=['POST'])
def upload_image():
    if 'image' not in request.files:
        return jsonify({"success": False, "message": "No file part"})

    file = request.files['image']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"})

    if file:
        filename = file.filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        image_url = f"/{UPLOAD_FOLDER}/{filename}"  # Assuming you serve static files from this folder
        return jsonify({"success": True, "imageUrl": image_url})

    return jsonify({"success": False, "message": "Upload failed"})

# Function to scrape product data from USG Store
def scrape_product(url):
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))

    # Initialize an empty product dictionary to avoid UnboundLocalError
    product = {}

    try:
        response = session.get(url)
        response.raise_for_status()  # Raise an error for invalid responses
        soup = BeautifulSoup(response.content, 'html.parser')

        # Scraping product details
        product['Title'] = soup.find('h3').get_text(strip=True)  # Assuming h3 is for product title
        product['Brand'] = 'Jordan'  # Static value for this site
        product['Color'] = soup.find('h4').get_text(strip=True)  # Assuming color is in h4
        product['Gender'] = 'Unisex'
        product['Material'] = 'Leather'
        product['Age group'] = 'Adult'

        # Check if there is embedded JavaScript containing product data
        script_tag = soup.find('script', text=re.compile('new Shopify\\.OptionSelectors'))
        
        if script_tag:
            # Extract the text of the script tag
            script_content = script_tag.string

            # Use regex to find specific product fields like 'SKU', 'Size', etc.
            size_match = re.search(r'"Size":"(.*?)"', script_content)
            sku_match = re.search(r'"sku":"(.*?)"', script_content)
            barcode_match = re.search(r'"barcode":"(.*?)"', script_content)
            weight_match = re.search(r'"weight":(\d+)', script_content)
            quantity_match = re.search(r'"inventory_quantity":(\d+)', script_content)
            id_match = re.search(r'"id":(\d+)', script_content)

            # Extract and store the found values
            product['Size'] = size_match.group(1) if size_match else 'Size not found'
            product['SKU'] = sku_match.group(1) if sku_match else 'SKU not found'
            product['Barcode'] = barcode_match.group(1) if barcode_match else 'Barcode not found'
            product['Weight'] = weight_match.group(1) if weight_match else 'Weight not found'
            product['Quantity'] = quantity_match.group(1) if quantity_match else 'Quantity not found'
            product['id'] = id_match.group(1) if id_match else 'ID not found'

        else:
            print('No JavaScript object found containing product details')

        # Find the main div containing the thumbnail images
        thumbnail_slider = soup.find('div', class_='product-thumbnail-slider')

        if thumbnail_slider:
            # Get all the divs with class 'thumbnail-slide'
            thumbnail_slides = thumbnail_slider.find_all('div', class_='thumbnail-slide')

            # Check if there are at least two images
            if len(thumbnail_slides) >= 2:
                # Get the second image
                second_image = thumbnail_slides[1].find('img')

                if second_image and 'src' in second_image.attrs:
                    img_src = second_image['src']

                    # If the src starts with '//', add the https: prefix
                    if img_src.startswith('//'):
                        img_src = 'https:' + img_src
                    product['Image'] = img_src
                    
                    print(f'Second image URL: {img_src}')
                else:
                    print('Second image not found')
            else:
                print('Less than two images found')
        else:
            print('No thumbnail slider found')

        # Return the complete product dictionary
        print(f"Scraped product: {product}")
        return product

    except Exception as e:
        print(f"Error occurred: {e}")
        return None


        

    except requests.exceptions.RequestException as e:
        print(f"Error fetching URL: {e}")
        # Store the error message in the product dictionary
        product['Error'] = str(e)

    return product


# Shopify API integration (assuming shopify package is already installed and configured)
def connect_to_shopify(api_key, password, store_url):
    shop_url = f"https://{api_key}:{password}@{store_url}.myshopify.com/admin"
    shopify.ShopifyResource.set_site(shop_url)

def upload_to_shopify(product_data, sku, shipping_info):
    new_product = shopify.Product()
    new_product.title = product_data['Title']
    new_product.body_html = product_data['Product detail']
    new_product.vendor = product_data['Brand']
    new_product.product_type = 'Shoes'
    
    # Add SKU if provided by user
    product_sku = sku if sku != 'N/A' else product_data['SKU']

    # Adding product variants (size, SKU, etc.)
    new_product.variants = [{
        'price': '199.99',  # Example price
        'sku': product_sku,
        'barcode': product_data['GTIN/UPC/barcode'],
        'weight': product_data['Weight'],
        'inventory_quantity': product_data['Quantity'],
        'size': product_data['Size']
    }]
    new_product.save()

    # Set shipping and return policies as metafields
    metafields = [
        shopify.Metafield({
            'namespace': 'shipping',
            'key': 'shipping_weight',
            'value': shipping_info['Shipping weight'],
            'value_type': 'string'
        }),
        shopify.Metafield({
            'namespace': 'shipping',
            'key': 'shipping_policy',
            'value': shipping_info['Shipping policy'],
            'value_type': 'string'
        }),
        shopify.Metafield({
            'namespace': 'returns',
            'key': 'returns_policy',
            'value': shipping_info['Returns and refunds policy'],
            'value_type': 'string'
        })
    ]

    for metafield in metafields:
        new_product.add_metafield(metafield)

    return new_product

# Route to serve frontend
@app.route('/')
def index():
    product = {
        'Image': 'path_to_image',  # Replace with the actual image path or URL
        # Add other product fields here as necessary
        'Title': 'Air Jordan 1 Retro High OG "Midnight Navy"',
        'Category': 'Shoes',
        'Gender': 'Unisex'
    }
    return render_template('frontend.html', product=product)


# Route for scraping and storing data
@app.route('/scrape', methods=['POST'])
def scrape():
    global scraped_data
    data = request.json
    print("Received scraping request: ", data)  # Add print statement for debugging
    url = data.get('url')
    
    # Emit real-time updates
    socketio.emit('update', {'message': 'Starting scraping...'})
    time.sleep(1)  # Simulate delay

    # Scrape product data
    scraped_data = scrape_product(url)
    if isinstance(scraped_data, dict):  # Ensure it's a dictionary
        # socketio.emit('update', {'message': f"Scraped product: {scraped_data.get('Title', 'No title found')}"})
        socketio.emit('update', {'message': f"Scraped product: {scraped_data['Title']}", 'product': scraped_data})
        return jsonify(scraped_data)
    else:
        return "Error: Expected a dictionary", 500
    # socketio.emit('update', {'message': f'Scraped product: {scraped_data["Title"]}'})
    # return jsonify({'status': 'success', 'product': scraped_data})

# Route for uploading to Shopify
@app.route('/upload', methods=['POST'])
def upload():
    global scraped_data
    data = request.json
    sku = data.get('sku')

    # Emit real-time updates
    socketio.emit('update', {'message': 'Uploading to Shopify...'})

    # Shopify API credentials
    api_key = 'your_api_key'
    password = 'your_password'
    store_url = 'your_store_url'
    connect_to_shopify(api_key, password, store_url)

    # Define shipping and return policy
    shipping_info = {
        'Shipping weight': '1 kg',
        'Shipping policy': 'Standard shipping in 5-7 business days.',
        'Returns and refunds policy': 'Returns accepted within 30 days.'
    }

    # Upload product to Shopify
    upload_to_shopify(scraped_data, sku, shipping_info)
    socketio.emit('update', {'message': 'Upload completed!'})

    return jsonify({'status': 'success'})

# Run the app
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
